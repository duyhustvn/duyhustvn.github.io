---
title: "Kubernetes Thực Chiến: Giải Mã Kiến Trúc Container Runtime (CRI, containerd, runc) & Cẩm Nang Vận Hành với crictl / ctr"
date: 2026-11-21T15:20:00+07:00
draft: false
description: "Giải mã toàn diện kiến trúc Container Runtime trong Kubernetes từ kubelet, CRI, containerd, containerd-shim đến runc và Linux Kernel. Hướng dẫn thực chiến cấu hình endpoint, kiểm tra container/image với crictl và export/import image với ctr cho môi trường RKE2, K3s, Kubeadm và Air-gap."
summary: "Hiểu đúng bản chất Container Runtime trong Kubernetes (CRI, containerd, runc, pause container) và làm chủ bộ công cụ crictl / ctr để debug container, quản lý và export/import image thay thế Docker CLI trên Production."
tags: ["Kubernetes", "K8s", "Container Runtime", "containerd", "crictl", "ctr", "RKE2", "K3s", "DevOps", "Linux"]
categories: ["DevOps", "Kubernetes"]
showTableOfContents: true
---

Khi làm việc với Kubernetes hiện đại (từ v1.24 trở đi, sau khi `dockershim` chính thức bị loại bỏ), rất nhiều kỹ sư DevOps/SysAdmin từng quen với Docker gặp phải tình huống "đứng hình" khi SSH vào Worker Node: gõ `docker ps` hay `docker images` thì hệ thống báo **`command not found`**.

Lý do là Kubernetes đã chuyển sang giao tiếp trực tiếp với các **Container Runtime** chuẩn **CRI (Container Runtime Interface)** như `containerd` hoặc `CRI-O`. Khi Control Plane gặp sự cố khiến `kubectl` không phản hồi, hoặc khi bạn cần nạp image thủ công vào Node trong môi trường **Air-gap (offline)**, việc nắm vững kiến trúc Container Runtime cùng hai công cụ **`crictl`** và **`ctr`** là kỹ năng bắt buộc.

Bài viết này sẽ giúp bạn:
1. Hiểu rõ kiến trúc bên dưới từ `kubelet` → `CRI` → `containerd` → `runc` → Linux Kernel.
2. Nắm vững cách cấu hình socket endpoint (đặc biệt trên **RKE2 / K3s** và **Kubeadm**).
3. Thành thạo các bước thao tác kiểm tra container, xem image và **export / import image** bằng `crictl` và `ctr`.

---

## 🏗️ 1. Kiến trúc Container Runtime trong Kubernetes

Trước khi đi vào câu lệnh, hãy nhìn bức tranh tổng thể về cách một Container thực sự được khởi tạo trên Worker Node.

{{< mermaid >}}
flowchart TB
    Kubelet["kubelet<br/>(Node Agent)"]
    
    subgraph RuntimeLayer["⚙️ High-Level Runtime (CRI)"]
        CRI["CRI Plugin (gRPC Server)<br/>Unix Socket: containerd.sock"]
        Containerd["containerd Daemon<br/>(Quản lý Image, Snapshotter, Lifecycle)"]
        CRI --> Containerd
    end

    subgraph ShimLayer["🔗 Shim & Low-Level Runtime (OCI)"]
        Shim["containerd-shim-runc-v2<br/>(Giữ tiến trình container độc lập)"]
        Runc["runc<br/>(Khởi tạo container rồi thoát)"]
        Shim --> Runc
    end

    subgraph KernelLayer["🐧 Linux Kernel"]
        NS["Namespaces (PID, NET, MNT, IPC, UTS)"]
        CG["Cgroups (CPU, Memory Limits)"]
        FS["OverlayFS (Rootfs Image Layers)"]
    end

    Kubelet -->|"CRI gRPC API"| CRI
    Containerd -->|"Fork / Quản lý"| Shim
    Runc -->|"Syscalls: clone, unshare, pivot_root"| NS
    Runc --> CG
    Runc --> FS
{{< /mermaid >}}

### 1.1. CRI (Container Runtime Interface) là gì?
**CRI** là một bộ chuẩn giao tiếp (gRPC API) do Kubernetes định nghĩa. Nhờ có CRI, `kubelet` không cần biết bên dưới đang dùng công nghệ gì — nó chỉ việc gửi các yêu cầu chuẩn như `RunPodSandbox`, `CreateContainer`, `StartContainer`, `PullImage` qua một **Unix Socket**.

### 1.2. Phân biệt High-Level Runtime vs Low-Level Runtime
Một Container Runtime đầy đủ thường được chia làm 2 tầng rõ rệt:

1. **High-Level Runtime (`containerd`, `CRI-O`):**
   - Đóng vai trò "người quản lý": Lắng nghe lệnh từ `kubelet` qua CRI, tải (pull) và giải nén image, quản lý storage/snapshotter, mạng (gọi CNI plugin) và vòng đời của Pod/Container.
   - Trên hầu hết các cụm Kubernetes phổ biến hiện nay (Kubeadm, EKS, GKE, AKS, RKE2, K3s), **`containerd`** là lựa chọn mặc định.
2. **Low-Level Runtime (`runc`, `crun`):**
   - Đóng vai trò "thợ thi công": Tuân thủ chuẩn **OCI (Open Container Initiative)**. Nó nhận cấu hình JSON + thư mục rootfs từ `containerd`, gọi trực tiếp tới Linux Kernel để tạo **Namespaces** (cô lập tiến trình, mạng, filesystem) và **Cgroups** (giới hạn CPU/RAM), khởi chạy tiến trình ứng dụng, **sau đó tự thoát**.
3. **Vai trò của `containerd-shim`:**
   - Vì `runc` thoát ngay sau khi tạo container, `containerd-shim` đứng ra làm tiến trình cha (parent process) giữ cho container tiếp tục sống, thu thập exit code và giữ STDIN/STDOUT mở. Nhờ vậy, ngay cả khi bạn restart daemon `containerd` để nâng cấp, các container đang chạy vẫn **không bị chết**.

### 1.3. Bí mật về "Pause Container" trong mỗi Pod
Khi kiểm tra danh sách container trên node, bạn sẽ luôn thấy một image siêu nhỏ tên là `pause` (ví dụ: `docker.io/rancher/mirrored-pause:3.6`, dung lượng chỉ ~300KB).

Đây là **Pod Sandbox Container**. Mỗi khi Kubernetes tạo 1 Pod, `containerd` sẽ tạo container `pause` trước tiên để giữ **Network Namespace** (IP của Pod) và **IPC Namespace**. Toàn bộ các container ứng dụng của bạn trong cùng Pod đó sẽ `join` vào chung Network Namespace của `pause` — đó là lý do các container trong cùng một Pod có thể gọi nhau qua `localhost`!

---

## 🛠️ 2. Phân biệt `crictl` và `ctr` — Khi nào dùng cái nào?

Khi SSH vào Worker Node để thao tác với `containerd`, bạn sẽ gặp 2 công cụ CLI chính:

| Tiêu chí | `crictl` | `ctr` |
| :--- | :--- | :--- |
| **Nguồn gốc** | Công cụ chuẩn của cộng đồng Kubernetes (`cri-tools`) | Công cụ CLI nội bộ đi kèm với `containerd` |
| **Giao tiếp qua** | CRI gRPC API (nhìn ở góc độ Pod & Container của K8s) | `containerd` gRPC API trực tiếp (nhìn ở góc độ `containerd`) |
| **Hỗ trợ Namespace** | Tự động làm việc với workload của K8s | Phân tách namespace rõ ràng (K8s dùng namespace **`k8s.io`**) |
| **Thế mạnh chính** | Debug Pod/Container (`ps`, `pods`, `logs`, `exec`), kiểm tra & dọn dẹp image | **Export / Import image (`.tar`)**, thao tác cấp thấp với snapshot/content |

> **Quy tắc nhớ nhanh:** Dùng **`crictl`** để kiểm tra/debug Pod, Container, Logs. Dùng **`ctr -n k8s.io`** khi cần **Export / Import image** (hoặc thao tác trực tiếp vào kho lưu trữ của `containerd`).

---

## ⚙️ 3. Bước 1: Cấu hình Container Runtime Endpoint

Mặc định, nếu bạn gõ ngay `crictl ps` trên một máy chủ vừa cài RKE2 hoặc K3s, bạn có thể gặp lỗi kết nối socket vì mỗi bản phân phối Kubernetes đặt file `.sock` ở một vị trí khác nhau:

- **RKE2 / K3s:** `/run/k3s/containerd/containerd.sock`
- **Kubeadm (containerd chuẩn):** `/run/containerd/containerd.sock`
- **CRI-O:** `/var/run/crio/crio.sock`

### Cách 1: Thiết lập nhanh bằng biến môi trường (Session hiện tại)
Nếu bạn đang thao tác trên cụm **RKE2** hoặc **K3s**, hãy chạy 2 lệnh `export` sau để trỏ cả `crictl` và `ctr` về đúng socket của `containerd`:

```bash
export CONTAINER_RUNTIME_ENDPOINT=unix:///run/k3s/containerd/containerd.sock
export CONTAINERD_ADDRESS=/run/k3s/containerd/containerd.sock
```

*(Nếu dùng **Kubeadm** với `containerd` chuẩn, thay đường dẫn thành `/run/containerd/containerd.sock`).*

### Cách 2: Cấu hình cố định (Khuyên dùng trên Node vận hành)
Để không phải `export` lại mỗi lần mở phiên SSH mới, bạn có thể ghi cấu hình cho `crictl` vào `/etc/crictl.yaml`:

```bash
cat <<EOF | sudo tee /etc/crictl.yaml
runtime-endpoint: unix:///run/k3s/containerd/containerd.sock
image-endpoint: unix:///run/k3s/containerd/containerd.sock
timeout: 10
debug: false
EOF
```

Và thêm `export CONTAINERD_ADDRESS=/run/k3s/containerd/containerd.sock` vào `~/.bashrc` nếu thường xuyên dùng `ctr`.

---

## 🔍 4. Bước 2: Kiểm tra Container & Pod đang chạy với `crictl`

### 4.1. Liệt kê các Container đang chạy
```bash
crictl ps
```

Để xem cả những container đã dừng hoặc vừa bị crash (`Exited`):
```bash
crictl ps -a
```

### 4.2. Liệt kê các Pod (Sandboxes) trên Node
```bash
crictl pods
```

### 4.3. Debug trực tiếp khi `kubectl` không hoạt động
Khi `kube-apiserver` gặp sự cố, bạn vẫn có thể kiểm tra log hoặc truy cập vào container bằng `crictl`:

```bash
# Xem log của 1 container
crictl logs <container-id>
crictl logs -f --tail=100 <container-id>

# Truy cập shell vào bên trong container
crictl exec -it <container-id> sh

# Xem chi tiết cấu hình (IP, Mounts, Env, Cgroups) của container
crictl inspect <container-id>
```

---

## 📦 5. Bước 3: Quản lý, Export & Import Images (`crictl` & `ctr`)

### 5.1. Liệt kê danh sách Images trên Node
Dùng `crictl images` để xem toàn bộ container images đang được lưu trữ cục bộ trên node:

```bash
crictl images
```

**Kết quả mẫu trên một Node RKE2 chạy Cilium CNI:**
```text
IMAGE                                              TAG              IMAGE ID       SIZE
docker.io/library/busybox                          latest           a416a98b71e22  2.28MB
docker.io/rancher/klipper-lb                       v0.4.13          45b23dee28620  5.02MB
docker.io/rancher/local-path-provisioner           v0.0.24          b29384feb4b13  14.9MB
docker.io/rancher/mirrored-cilium-cilium-envoy     v1.34.4          8c1d4b92e1a10  70.4MB
docker.io/rancher/mirrored-cilium-cilium           v1.18.1          3f7a2c10d9b44  263MB
docker.io/rancher/mirrored-pause                   3.6              6270bb605e12e  301kB
docker.io/rancher/rke2-runtime                     v1.33.5-rke2r1   9e41b8a7c3d21  100MB
```

> **Lưu ý về lệnh Pull / Xóa Image với `crictl`:**
> - Tải image thủ công: `crictl pull docker.io/library/busybox:latest`
> - Xóa 1 image: `crictl rmi <image-id>`
> - Dọn dẹp các image không còn container nào sử dụng: `crictl rmi --prune`

### 5.2. Export & Import Image với `ctr` (Thay thế `docker save` / `docker load`)
Công cụ `crictl` không có tính năng export/import image ra file `.tar`. Để làm việc này (ví dụ: đóng gói image từ máy có Internet để mang vào cụm **Air-gap**, hoặc copy nhanh image giữa các node), ta phải dùng **`ctr`**.

> ⚠️ **CỰC KỲ QUAN TRỌNG — Flag `-n k8s.io`:**
> `containerd` phân chia dữ liệu theo **namespace**. Toàn bộ image và container của Kubernetes đều nằm trong namespace **`k8s.io`**. Nếu bạn quên tham số `-n k8s.io`, `ctr` sẽ mặc định thao tác trên namespace `default` — dẫn đến tình trạng export báo lỗi không tìm thấy image, hoặc import xong nhưng `kubelet` vẫn báo `ImagePullBackOff`!

**1. Export image ra file `.tar`:**
```bash
ctr -n k8s.io images export mirror.tar docker.io/rancher/mirrored-pause:3.6
```
*(Bạn cũng có thể export nhiều image vào cùng một file `.tar` bằng cách liệt kê nối tiếp nhau sau tên file `mirror.tar`).*

**2. Chuyển file `mirror.tar` sang Node đích (bằng `scp` / USB / ổ cứng rời) và Import vào `containerd`:**
```bash
ctr -n k8s.io images import mirror.tar
```

**3. Xác nhận lại image đã vào đúng kho của Kubernetes:**
```bash
crictl images | grep mirrored-pause
```

Nếu image đã xuất hiện trong danh sách của `crictl images`, `kubelet` trên node đó có thể sử dụng ngay lập tức mà không cần kéo từ Registry bên ngoài (miễn là `imagePullPolicy` trong Pod spec là `IfNotPresent` hoặc `Never`).

---

## 📋 6. Bảng Tra Cứu Nhanh (Cheatsheet: Docker vs `crictl` / `ctr`)

Nếu bạn đang chuyển từ thói quen dùng Docker CLI sang môi trường Kubernetes chuẩn CRI, hãy lưu lại bảng đối chiếu này:

| Tác vụ | Lệnh Docker cũ | Lệnh Kubernetes CRI mới (`crictl` / `ctr`) |
| :--- | :--- | :--- |
| **Xem container đang chạy** | `docker ps` | `crictl ps` |
| **Xem tất cả container** | `docker ps -a` | `crictl ps -a` |
| **Xem danh sách Pod** | *(Không có)* | `crictl pods` |
| **Xem log container** | `docker logs -f <id>` | `crictl logs -f <id>` |
| **Truy cập shell container** | `docker exec -it <id> sh` | `crictl exec -it <id> sh` |
| **Xem danh sách images** | `docker images` | `crictl images` |
| **Tải image về node** | `docker pull <image>` | `crictl pull <image>` |
| **Xóa image rác** | `docker image prune -a` | `crictl rmi --prune` |
| **Export image ra file `.tar`** | `docker save -o app.tar <image>` | `ctr -n k8s.io images export app.tar <image>` |
| **Import image từ file `.tar`** | `docker load -i app.tar` | `ctr -n k8s.io images import app.tar` |

---

## ⚠️ 7. Những lỗi thường gặp & Lưu ý thực chiến

1. **Import image thành công nhưng Pod vẫn báo `ErrImagePull` / `ImagePullBackOff`:**
   - **Nguyên nhân 1:** Bạn quên `-n k8s.io` khi chạy `ctr images import`, khiến image nằm ở namespace `default` thay vì `k8s.io`.
   - **Nguyên nhân 2:** Manifest của Pod đang cấu hình `imagePullPolicy: Always` trong môi trường Air-gap. Khi đặt `Always`, `kubelet` vẫn cố gắng kết nối ra Registry để kiểm tra digest. Hãy đổi thành `IfNotPresent` hoặc `Never`.
   - **Nguyên nhân 3:** Sai tên tag hoặc registry prefix (ví dụ image trong tar là `docker.io/rancher/mirrored-pause:3.6` nhưng Pod gọi `rancher/mirrored-pause:3.6` hoặc thiếu `docker.io/`). Hãy kiểm tra kỹ bằng `crictl images`.

2. **Lỗi `connect: no such file or directory` khi chạy `crictl` hoặc `ctr`:**
   - Do chưa `export CONTAINER_RUNTIME_ENDPOINT` / `CONTAINERD_ADDRESS` hoặc trỏ nhầm đường dẫn socket giữa Kubeadm (`/run/containerd/containerd.sock`) và RKE2/K3s (`/run/k3s/containerd/containerd.sock`).

3. **Ổ cứng Worker Node đầy bất ngờ (`DiskPressure`):**
   - Trong quá trình CI/CD deploy liên tục, các tag image cũ không tự biến mất ngay lập tức. Nếu `kubelet` image garbage collection chưa kịp dọn, bạn có thể kiểm tra dung lượng bằng `crictl images` và chủ động giải phóng với `crictl rmi --prune`.

---

## 🎯 Tổng kết

Hiểu rõ kiến trúc **CRI → `containerd` → `containerd-shim` → `runc`** giúp bạn không còn coi container như một "hộp đen", đồng thời tự tin xử lý sự cố ở tầng sâu nhất của Worker Node:
- Luôn cấu hình đúng **Socket Endpoint** (`/run/k3s/containerd/containerd.sock` cho RKE2/K3s hoặc `/run/containerd/containerd.sock` cho Kubeadm).
- Dùng **`crictl`** để theo dõi Pod, Container, Logs và Images ở góc nhìn Kubernetes.
- Dùng **`ctr -n k8s.io`** để **export / import** image an toàn cho môi trường Air-gap hoặc khi cần chuyển image thủ công giữa các máy chủ.
