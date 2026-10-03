---
title: "Kubernetes Thực Chiến (Phần 1): Giải Mã Kiến Trúc & Cẩm Nang Vận Hành Node Chuyên Sâu (Labels, Taints & Drain)"
date: 2026-10-03T17:00:00+07:00
draft: false
description: "Bài viết mở đầu series 'Kubernetes Thực Chiến: Giải Thích & Vận Hành'. Đi sâu vào bản chất kiến trúc của Node trong K8s: Vòng đời node, phân bổ tải với Node Labels & Node Affinity, cơ chế Taints & Tolerations xua đuổi Pod, và quy trình chuẩn 3 bước bảo trì / loại bỏ Node an toàn (Cordon, Drain, Delete) trên Production."
summary: "Làm chủ Node trong Kubernetes từ kiến trúc đến vận hành thực chiến: Quản trị Node Labels, thiết lập rào chắn Taints & Tolerations, và quy trình Cordon - Drain - Delete an toàn tuyệt đối, tránh downtime cho hệ thống Production."
tags: ["Kubernetes", "K8s", "DevOps", "SysAdmin", "Cloud-Native", "Container", "Linux", "Production"]
categories: ["DevOps", "Kubernetes"]
showTableOfContents: true
---

Chào mừng bạn đến với chuỗi bài viết **"Kubernetes Thực Chiến: Giải Thích & Vận Hành Chuyên Sâu"**! 

Khi tiếp cận với Kubernetes, hầu hết lập trình viên và kỹ sư vận hành thường bắt đầu từ các đối tượng quen thuộc như Pod, Deployment hay Service. Tuy nhiên, ở tầng hạ tầng cốt lõi, toàn bộ khối lượng công việc (workloads) khổng lồ đó đều phải đáp xuống một nền tảng vật lý duy nhất: **Node (Máy chủ tính toán)**.

Node chính là những "lực sĩ cử tạ" mang trên vai CPU, RAM, ổ cứng và băng thông mạng của cả cụm cluster. Trong quá trình vận hành thực tế ở môi trường Production, bạn sẽ liên tục đối mặt với các bài toán:
- Làm sao để ép các Pod cơ sở dữ liệu (Database) hoặc các tác vụ nặng chỉ được phép chạy trên máy chủ có ổ cứng NVMe/SSD tốc độ cao?
- Làm sao để "cách ly" một nhóm node dành riêng cho API Gateway, Machine Learning (GPU) mà không bị các Pod dịch vụ thông thường chiếm dụng tài nguyên?
- Và đặc biệt: Khi một máy chủ vật lý cần nâng cấp RAM, cập nhật Kernel OS, hoặc cần xóa bỏ để giảm quy mô (Scale-down), **làm thế nào để di dời hàng trăm Pod đang chạy ra ngoài một cách êm ái mà không làm rớt bất kỳ một request nào của khách hàng?**

Bài viết đầu tiên trong series này sẽ giúp bạn giải mã toàn diện bản chất kiến trúc của Node và cung cấp cẩm nang vận hành thực chiến: Từ **Node Labels**, **Taints & Tolerations**, cho đến quy trình chuẩn **Cordon - Drain - Delete**.

---

## 🏗️ 1. Bản chất của Node trong Kubernetes là gì?

Trong Kubernetes, một **Node** là một máy chủ tính toán riêng lẻ — có thể là máy chủ vật lý (Bare-metal) hoặc máy ảo (Virtual Machine - VM trên AWS, GCP, Proxmox, VMware).

Một cụm Kubernetes tiêu chuẩn luôn được phân định thành 2 nhóm vai trò:
1. **Control Plane Nodes (Master):** Bộ não điều hành cụm (chạy `kube-apiserver`, `etcd`, `kube-scheduler`, `kube-controller-manager`).
2. **Worker Nodes:** Các node chuyên trách việc chạy các container ứng dụng của bạn.

{{< mermaid >}}
flowchart TB
    subgraph ControlPlane["☸️ Kubernetes Control Plane"]
        API["kube-apiserver"]
        ETCD[("etcd Storage")]
        SCHED["kube-scheduler"]
        API <--> ETCD
        API <--> SCHED
    end

    subgraph WorkerNode["⚙️ Worker Node Architecture"]
        subgraph NodeComponents["Thành phần nền tảng Node"]
            Kubelet["<b>kubelet</b><br/>(Agent quản lý Node & Pod)"]
            Proxy["<b>kube-proxy</b><br/>(Quản lý iptables / IPVS Service)"]
            CRI["<b>Container Runtime</b><br/>(containerd / CRI-O)"]
        end

        subgraph PodContainers["Không gian Workload"]
            P1["Pod A (Container 1)"]
            P2["Pod B (Container 2)"]
        end

        Kubelet -->|Điều khiển qua CRI API| CRI
        CRI --> P1
        CRI --> P2
    end

    API <==>|Giao tiếp HTTPS (Port 10250 / 6443)| Kubelet
    Proxy -.->|Theo dõi Service & Endpoints| API
{{< /mermaid >}}

### 1.1. Giải phẫu kiến trúc bên trong một Worker Node
Để một máy chủ có thể gia nhập và vận hành trơn tru như một Worker Node trong K8s, nó bắt buộc phải chạy 3 tiến trình nền tảng:
- **`kubelet` (Đội trưởng thi công):** Là agent chính chạy trên từng node. `kubelet` thường xuyên theo dõi các `PodSpec` do API Server gửi xuống, chỉ thị cho Container Runtime khởi tạo container, đồng thời gửi báo cáo sức khỏe (Heartbeat) và tài nguyên của Node về Control Plane thông qua đối tượng `Node Lease` (trong namespace `kube-node-lease`).
- **Container Runtime (CRI - Container Runtime Interface):** Động cơ thực thi container trực tiếp trên hệ điều hành (hiện nay tiêu chuẩn phổ biến nhất là `containerd` hoặc `CRI-O`, thay thế hoàn toàn Docker Engine cũ).
- **`kube-proxy` (Cảnh sát giao thông mạng):** Theo dõi các thay đổi của Service và Endpoint trong cluster để cập nhật bảng định tuyến `iptables` hoặc `IPVS` trên Linux Kernel của node, đảm bảo lưu lượng mạng được chuyển tiếp chính xác đến Pod đích.

### 1.2. Vòng đời và Trạng thái của Node (Node Status)
Khi bạn chạy lệnh kiểm tra danh sách máy chủ:

```bash
kubectl get nodes -o wide
```

```text
NAME             STATUS   ROLES           AGE   VERSION   INTERNAL-IP    OS-IMAGE             KERNEL-VERSION      CONTAINER-RUNTIME
node-master-01   Ready    control-plane   45d   v1.30.2   192.168.1.11   Ubuntu 22.04.4 LTS   5.15.0-117-generic  containerd://1.7.18
node-worker-01   Ready    worker          45d   v1.30.2   192.168.1.21   Ubuntu 22.04.4 LTS   5.15.0-117-generic  containerd://1.7.18
node-worker-02   Ready    worker          45d   v1.30.2   192.168.1.22   Ubuntu 22.04.4 LTS   5.15.0-117-generic  containerd://1.7.18
```

Cột **`STATUS`** phản ánh trực tiếp tình trạng của Node:
- **`Ready`:** Node đang khỏe mạnh, `kubelet` gửi heartbeat đều đặn và sẵn sàng tiếp nhận Pod.
- **`NotReady`:** Node gặp sự cố (Kubelet bị crash, mất kết nối mạng, hoặc quá tải tài nguyên), không gửi được heartbeat trong khoảng thời gian quy định (mặc định quá 40 giây).
- **`SchedulingDisabled`:** Node đã bị khóa (Cordoned), không cho phép lập lịch đặt Pod mới vào node này.
- **`Unknown`:** Control Plane hoàn toàn mất dấu node (thường do node bị sập nguồn đột ngột hoặc rớt mạng diện rộng).

Bây giờ, chúng ta sẽ bước vào 3 công cụ vận hành cốt tử để điều khiển hành vi của Node: **Labels**, **Taints & Tolerations**, và quy trình **Drain**.

---

## 🏷️ 2. Quản lý và Phân loại Node với Node Labels

### 2.1. Node Label là gì và tại sao lại tối quan trọng?
Trong một cụm K8s lớn, bạn có thể có hàng chục đến hàng trăm nodes với cấu hình phần cứng rất khác nhau:
- Node có ổ cứng SSD / NVMe siêu tốc.
- Node có card màn hình đồ họa GPU (Nvidia A100/H100) chuyên chạy AI/LLM.
- Node nằm ở Data Center Zone A, Node nằm ở Zone B.
- Node nằm ở vùng mạng DMZ (để chạy Ingress Gateway đón traffic từ ngoài Internet).

**Node Label (Nhãn)** là các cặp khóa-giá trị (`key=value`) được gắn vào metadata của Node. Kubernetes Scheduler (`kube-scheduler`) sẽ căn cứ vào các Label này kết hợp với trường `nodeSelector` hoặc `nodeAffinity` trong Pod Manifest để quyết định xem Pod nào sẽ được chạy trên Node nào.

{{< mermaid >}}
flowchart LR
    subgraph PodManifest["Pod Deployment"]
        Pod["Pod: Payment-Database<br/><b>nodeSelector: disk-type=ssd</b>"]
    end

    subgraph Scheduler["kube-scheduler"]
        Decision{"Phân tích<br/>Node Labels"}
    end

    subgraph Nodes["Cluster Nodes"]
        N1["worker-node-1<br/>disk-type=hdd"]
        N2["worker-node-2<br/><b>disk-type=ssd</b>"]
    end

    Pod --> Decision
    Decision -.->|Loại bỏ (Mismatch)| N1
    Decision ==>|Khớp nhãn (Scheduled)| N2
{{< /mermaid >}}

### 2.2. Các lệnh thao tác với Node Label (Hands-on CLI)

#### 🔍 Xem danh sách nhãn hiện có trên Node
Mỗi node khi vừa gia nhập cluster đã có sẵn một loạt label hệ thống (kiến trúc CPU, OS, hostname,...). Bạn có thể xem toàn bộ nhãn bằng cờ `--show-labels`:

```bash
kubectl get nodes --show-labels
```

Để lọc ra các cột nhãn cụ thể cho dễ quan sát, sử dụng cờ `-L`:

```bash
kubectl get nodes -L kubernetes.io/os,kubernetes.io/arch,disk-type
```

```text
NAME             STATUS   ROLES    AGE   VERSION   OS      ARCH    DISK-TYPE
node-worker-01   Ready    worker   45d   v1.30.2   linux   amd64   <none>
node-worker-02   Ready    worker   45d   v1.30.2   linux   amd64   <none>
```

#### ➕ Gán nhãn cho Node (Set Label)
Cú pháp cơ bản:
```bash
kubectl label node <node-name> <label-key>=<label-value>
```

*Ví dụ thực tế:* Gán nhãn đánh dấu node `worker-node-1` đang sở hữu ổ cứng SSD:
```bash
kubectl label node worker-node-1 disk-type=ssd
```

Kết quả thông báo:
```text
node/worker-node-1 labeled
```

Bạn cũng có thể gán nhãn cho vai trò môi trường mạng:
```bash
kubectl label node k8s-gateway-01 role=gateway zone=dmz
```

#### ✏️ Ghi đè nhãn đã tồn tại (Override Label)
Nếu bạn cố tình gán một nhãn đã có sẵn giá trị:
```bash
kubectl label node worker-node-1 disk-type=nvme
```
Kubernetes sẽ báo lỗi để bảo vệ hệ thống:
```text
error: 'disk-type' already has a value (ssd), and --overwrite is false
```

Để cập nhật lại giá trị mới, bạn **bắt buộc** phải truyền cờ `--overwrite`:
```bash
kubectl label node --overwrite worker-node-1 disk-type=nvme
```
```text
node/worker-node-1 labeled
```

#### ❌ Xóa nhãn khỏi Node (Delete Label)
Để xóa một label, hãy thêm dấu trừ `-` ngay sau tên `label-key`:

```bash
kubectl label node <node-name> <label-key>-
```

*Ví dụ:* Gỡ nhãn `disk-type` khỏi node `worker-node-1`:
```bash
kubectl label node worker-node-1 disk-type-
```
```text
node/worker-node-1 unlabeled
```

### 2.3. Ứng dụng thực chiến: Điều hướng Pod với `nodeSelector`
Sau khi đã gán nhãn `disk-type=ssd` cho node, lập trình viên có thể chỉ định Pod yêu cầu I/O cao chạy đúng trên node này thông qua trường `nodeSelector`:

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: redis-cache
spec:
  replicas: 2
  selector:
    matchLabels:
      app: redis-cache
  template:
    metadata:
      labels:
        app: redis-cache
    spec:
      containers:
      - name: redis
        image: redis:7-alpine
        ports:
        - containerPort: 6379
      nodeSelector:
        disk-type: ssd # 🎯 Pod chỉ được schedule vào node có nhãn disk-type=ssd
```

---

## 🚫 3. Cơ chế Taints & Tolerations: "Xua đuổi & Miễn nhiễm"

Nếu như **Node Labels** (kết hợp `nodeSelector` / `nodeAffinity`) hoạt động như một **thỏi nam châm thu hút Pod** về phía Node, thì **Taints** lại có bản chất hoàn toàn trái ngược:

> 📌 **Quy tắc vàng:**
> - **Taints** được áp dụng lên **Node** để đóng vai trò như một **"hàng rào cấm / xua đuổi" (Repelling mechanism)**, ngăn không cho các Pod lạ đặt chân vào.
> - **Tolerations** được khai báo bên trong **Pod**, đóng vai trò như một **"tấm vé thông hành / miễn nhiễm"** cho phép Pod được lập lịch vào Node có Taint tương ứng.

{{< mermaid >}}
flowchart TD
    subgraph Nodes["Cluster Nodes"]
        NormalNode["Worker Node 1<br/>(Không Taint)"]
        TaintedNode["Worker Node 2<br/><b>Taint: dedicated=gpu:NoSchedule</b>"]
    end

    subgraph Pods["Pods đang cần Schedule"]
        PNormal["Pod Web API<br/>(Không có Toleration)"]
        PGPU["Pod AI Model Training<br/><b>Toleration: dedicated=gpu</b>"]
    end

    PNormal -->|Chạy bình thường| NormalNode
    PNormal -.->|🚫 Bị xua đuổi / Chặn lại| TaintedNode

    PGPU -->|Có vé thông hành| TaintedNode
{{< /mermaid >}}

### 3.1. Cấu trúc và 3 Cấp độ Effect của Taint
Một Taint luôn có định dạng:
```text
<key>=<value>:<effect>
```
Trong đó, **`effect`** quy định cách Node đối xử với các Pod **không có Toleration**:

| Effect | Ý nghĩa & Hành vi thực tế |
| :--- | :--- |
| **`NoSchedule`** | **Chặn Pod mới:** Kubernetes Scheduler sẽ tuyệt đối không xếp các Pod mới (không có toleration phù hợp) lên Node này. Tuy nhiên, các Pod cũ đang chạy sẵn từ trước trên Node **hoàn toàn không bị ảnh hưởng** (vẫn sống bình thường). |
| **`PreferNoSchedule`** | **Khuyến nghị mềm:** K8s Scheduler sẽ *cố gắng tránh* xếp Pod lên Node này. Tuy nhiên, nếu toàn bộ cluster đã cạn kiệt tài nguyên và không còn node nào khác, Scheduler vẫn có thể miễn cưỡng đưa Pod vào đây. |
| **`NoExecute`** | **Lệnh trục xuất khẩn cấp:** Không những chặn Pod mới, mà còn **LẬP TỨC TRỤC XUẤT (EVICT)** tất cả các Pod đang chạy trên Node nếu chúng không có Toleration tương ứng! |

> [!TIP]
> **Ứng dụng của `NoExecute`:** Thường dùng khi Node chuẩn bị bảo trì khẩn cấp hoặc khi Node gặp lỗi phần cứng. Bạn cũng có thể thiết lập `tolerationSeconds` trên Pod để tạo thời gian ân hạn: *"Nếu Node bị taint NoExecute, hãy cho phép Pod của tôi sống thêm 300 giây trước khi bị xóa"*.

### 3.2. Các Built-in Taints tự động của Kubernetes
Khi một node gặp trục trặc, chính hệ thống Kubernetes Node Lifecycle Controller sẽ tự động gán các Taint sau lên Node để bảo vệ các ứng dụng:
- `node.kubernetes.io/not-ready`: Node đang ở trạng thái NotReady.
- `node.kubernetes.io/unreachable`: Node không phản hồi heartbeat.
- `node.kubernetes.io/memory-pressure`: Node sắp cạn sạch RAM.
- `node.kubernetes.io/disk-pressure`: Node sắp đầy ổ đĩa (Root filesystem hoặc Image filesystem).
- `node.kubernetes.io/network-unavailable`: Mạng CNI của node chưa khởi tạo xong.

### 3.3. Các lệnh thực chiến với Taints (Hands-on CLI)

#### 🔍 Xem danh sách Taints trên các Node
Lệnh chuẩn để liệt kê ngắn gọn taints của toàn bộ node trong cụm:

```bash
kubectl get nodes -o custom-columns=NAME:.metadata.name,TAINTS:.spec.taints --no-headers
```

Hoặc kiểm tra chi tiết trên một node cụ thể:
```bash
kubectl describe node k8s-gateway-01 | grep -i taints
```

#### ➕ Thiết lập Taint cho Node (Set Taint)
Cú pháp cơ bản:
```bash
kubectl taint nodes <node-name> <key>=<value>:<effect>
```

*Ví dụ 1:* Dành riêng máy chủ `k8s-gateway-01` để làm Ingress Gateway, ngăn các Pod ứng dụng thông thường nhảy vào chạy:
```bash
kubectl taint nodes k8s-gateway-01 gateway=true:NoSchedule
```
```text
node/k8s-gateway-01 tainted
```

*Ví dụ 2:* Dành riêng máy chủ có card đồ họa cho các tác vụ Machine Learning:
```bash
kubectl taint nodes k8s-gpu-01 dedicated=gpu:NoSchedule
```

#### 🎫 Cấu hình Toleration trong Pod Manifest
Để một Pod được phép chạy trên node `k8s-gateway-01` vừa bị Taint ở trên, manifest của Pod phải chứa trường `tolerations`:

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: ingress-nginx-controller
spec:
  replicas: 1
  template:
    spec:
      containers:
      - name: nginx
        image: nginx:alpine
      tolerations:
      - key: "gateway"
        operator: "Equal"
        value: "true"
        effect: "NoSchedule"
```

> [!NOTE]
> Nếu bạn muốn Pod chịu đựng được mọi Taint có key là `gateway` mà không quan tâm giá trị `value` là gì, bạn có thể dùng toán tử `Exists`:
> ```yaml
> tolerations:
> - key: "gateway"
>   operator: "Exists"
>   effect: "NoSchedule"
> ```

#### ❌ Xóa Taint khỏi Node (Remove Taint)
Để xóa một Taint, hãy thêm dấu trừ `-` ở cuối lệnh:

**1. Xóa Taint cụ thể trên một Node:**
```bash
kubectl taint nodes k8s-gateway-01 gateway=true:NoSchedule-
```
Hoặc chỉ cần chỉ định key và dấu `-`:
```bash
kubectl taint nodes k8s-gateway-01 gateway-
```
```text
node/k8s-gateway-01 untainted
```

**2. Xóa Taint trên TOÀN BỘ các Node trong cụm:**
Khi bạn muốn gỡ bỏ một rào cản trên tất cả các máy chủ cùng một lúc:
```bash
kubectl taint nodes --all <key>-
```

*Ví dụ:* Gỡ bỏ taint `gateway` trên toàn cluster:
```bash
kubectl taint nodes --all gateway-
```

> [!IMPORTANT]
> **Kinh nghiệm thực tế (Single-Node Cluster):**
> Mặc định khi cài đặt cụm Kubernetes, các Node Control Plane (Master) luôn bị tự động gắn taint:
> `node-role.kubernetes.io/control-plane:NoSchedule` (hoặc `node-role.kubernetes.io/master:NoSchedule` trên các bản cũ).
> Nếu bạn đang dựng cụm Lab nhỏ chỉ có 1 node duy nhất (vừa làm Master vừa làm Worker), Pod của bạn sẽ không bao giờ chạy được và bị kẹt ở trạng thái `Pending`. Bạn chỉ cần gỡ taint master bằng lệnh:
> ```bash
> kubectl taint nodes --all node-role.kubernetes.io/control-plane:NoSchedule-
> ```

---

## 🛠️ 4. Quy trình Chuẩn Vận Hành Bảo Trì & Loại Bỏ Node (Cordon, Drain & Delete)

Trong thực tế vận hành hạ tầng Production, có 2 kịch bản bạn sẽ gặp rất thường xuyên:
1. **Bảo trì tạm thời (Temporary Maintenance):** Khởi động lại máy chủ để cập nhật OS Kernel, bảo trì hệ thống tản nhiệt hoặc thay card mạng. Sau khi xong sẽ đưa Node trở lại hoạt động bình thường.
2. **Loại bỏ vĩnh viễn (Decommission / Node Removal):** Hủy máy ảo trên Cloud để giảm chi phí (Scale-down), hoặc thanh lý máy chủ vật lý đã hỏng hóc.

Quy trình chuẩn kỹ thuật luôn bao gồm **3 giai đoạn tuần tự**:

{{< mermaid >}}
sequenceDiagram
    autonumber
    actor Admin as SysAdmin / DevOps
    participant API as kube-apiserver
    participant Sched as kube-scheduler
    participant Node as Worker Node
    participant Other as Other Worker Nodes

    Note over Admin,Other: Giai đoạn 1: Phong tỏa Node (Cordon)
    Admin->>API: kubectl cordon <node-name>
    API->>Node: Đánh dấu spec.unschedulable = true
    Sched-->>Node: 🚫 Từ chối xếp mọi Pod mới vào node

    Note over Admin,Other: Giai đoạn 2: Di tản an toàn (Drain)
    Admin->>API: kubectl drain <node-name> --ignore-daemonsets --delete-emptydir-data
    API->>Node: Gửi tín hiệu Eviction API (SIGTERM) tới các Pod
    API->>Other: Controller tạo Pod mới thay thế trên các Node khác
    Node-->>API: Pods kết thúc xử lý (Graceful Termination) hoàn tất
    Note over Node: Node hoàn toàn sạch bóng Pods!

    Note over Admin,Other: Giai đoạn 3: Bảo trì hoặc Xóa bỏ
    alt Trường hợp 1: Bảo trì xong, mở lại Node
        Admin->>API: kubectl uncordon <node-name>
        API->>Node: spec.unschedulable = false (Sẵn sàng nhận Pod)
    else Trường hợp 2: Xóa vĩnh viễn Node
        Admin->>API: kubectl delete node <node-name>
        Admin->>Node: Dọn dẹp Kubelet / Reset hệ điều hành
    end
{{< /mermaid >}}

---

### Bước 1: Cordon — Phong tỏa Node
Lệnh `cordon` thông báo cho `kube-scheduler` biết rằng node này đang bị khóa, **không được phép đặt thêm bất kỳ Pod mới nào vào đây nữa**:

```bash
kubectl cordon <node-name>
```

*Ví dụ:*
```bash
kubectl cordon node-worker-01
```
```text
node/node-worker-01 cordoned
```

Kiểm tra trạng thái node:
```bash
kubectl get nodes
```
```text
NAME             STATUS                     ROLES    AGE   VERSION
node-worker-01   Ready,SchedulingDisabled   worker   45d   v1.30.2
node-worker-02   Ready                      worker   45d   v1.30.2
```

> 💡 **Điểm cần lưu ý:** Lệnh `cordon` chỉ ngăn chặn Pod mới. **Các Pod hiện tại đang chạy trên node vẫn tiếp tục hoạt động bình thường.**

---

### Bước 2: Drain — Di tản toàn bộ Pods ra khỏi Node một cách êm ái
Sau khi đã cordon node, bước tiếp theo là di chuyển các Pod đang chạy trên node này sang các node khác trong cụm cluster.

Lệnh `drain` sẽ gọi trực tiếp đến **Eviction API** của Kubernetes. Thay vì ngắt điện cưỡng chế container (kill process), K8s sẽ:
1. Gửi tín hiệu `SIGTERM` đến container bên trong Pod.
2. Chờ đợi trong khoảng thời gian Grace Period (mặc định 30 giây) để ứng dụng hoàn tất các transaction cơ sở dữ liệu đang dở dang và đóng các kết nối HTTP/TCP đang mở.
3. Đồng thời, các Controller (Deployment, ReplicaSet) sẽ lập tức phát hiện số lượng replica bị giảm và ra lệnh cho Scheduler khởi tạo các Pod mới thay thế trên các node còn khỏe mạnh khác.

#### ⚠️ Giải mã các cờ (flags) sinh tử khi chạy Drain:

Nếu bạn chỉ chạy trơn lệnh `kubectl drain <node-name>`, khả năng rất cao là lệnh sẽ lập tức bị từ chối với hàng loạt thông báo lỗi. Bạn cần hiểu rõ các cờ sau:

##### 1. `--ignore-daemonsets`
Các DaemonSet (như Pod CNI Calico/Flannel, Pod thu thập log Promtail, Pod giám sát Node-Exporter) là những tiến trình bắt buộc phải chạy trên **tất cả** các node. 
Bạn không thể di tản một DaemonSet sang node khác được. Do đó, nếu không có cờ `--ignore-daemonsets`, lệnh drain sẽ dừng lại ngay lập tức.
- **Ý nghĩa:** Cho phép bỏ qua không evict các Pod thuộc DaemonSet.

##### 2. `--delete-emptydir-data`
Nếu một Pod sử dụng volume dạng `emptyDir` (thường dùng làm bộ nhớ đệm cache tạm thời hoặc chứa scratch log), dữ liệu trong thư mục này sẽ **bị xóa vĩnh viễn** khi Pod bị tiêu hủy.
- **Ý nghĩa:** Bạn xác nhận với Kubernetes rằng: *"Tôi chấp nhận việc dữ liệu tạm thời trong emptyDir của các Pod này sẽ bị xóa"*.

##### 3. `--force`
Nếu trên node có những Pod "mồ côi" (Bare Pods — Pod được tạo trực tiếp bằng lệnh `kubectl run` chứ không thông qua Deployment, StatefulSet hay ReplicaSet), khi bị drain thì Pod này sẽ **biến mất hoàn toàn** mà không có Controller nào dựng lại nó.
- **Ý nghĩa:** Ép buộc trục xuất cả những Pod mồ côi này.

#### 🚀 Lệnh Drain hoàn chỉnh chuẩn Production:
```bash
kubectl drain <node-name> --delete-emptydir-data --ignore-daemonsets --force
```

*Ví dụ thực tế:*
```bash
kubectl drain node-worker-01 --delete-emptydir-data --ignore-daemonsets --force
```

*Output thực tế trên Terminal:*
```text
node/node-worker-01 already cordoned
WARNING: ignoring DaemonSet-managed Pods: kube-system/calico-node-8k4lx, kube-system/kube-proxy-m4nv9
evicting pod default/payment-service-598d975bd-c6l8z
evicting pod default/order-service-674cb5499-f2x4p
pod default/order-service-674cb5499-f2x4p evicted
pod default/payment-service-598d975bd-c6l8z evicted
node/node-worker-01 drained
```

Lúc này, toàn bộ Pod ứng dụng đã được di chuyển an toàn sang các node khác, node `node-worker-01` hoàn toàn "sạch bóng" và bạn có thể an tâm tắt máy hoặc can thiệp phần cứng.

---

### Bước 3: Khôi phục Node hoặc Xóa vĩnh viễn khỏi Cụm

#### Kịch bản A: Sau khi bảo trì xong — Mở lại Node (Uncordon)
Sau khi bạn đã khởi động lại máy chủ và xác nhận `kubelet` đã hoạt động tốt, hãy bỏ phong tỏa để node tiếp tục phục vụ:

```bash
kubectl uncordon <node-name>
```

```bash
kubectl uncordon node-worker-01
```
```text
node/node-worker-01 uncordoned
```

> [!WARNING]
> **Hiện tượng Pod không tự nhảy lại node cũ:**
> Khi bạn uncordon, Kubernetes Scheduler sẽ **KHÔNG** tự động chuyển các Pod đang chạy ổn định ở các node khác quay trở về node này. K8s tôn trọng tính ổn định (tránh restart ứng dụng không cần thiết). Node sẽ chỉ nhận các Pod mới được tạo ra từ thời điểm này về sau. 
> Nếu bạn muốn phân bổ lại tải đều cho cụm, hãy cân nhắc sử dụng công cụ **Kubernetes Descheduler**.

---

#### Kịch bản B: Loại bỏ hoàn toàn Node khỏi Cụm (Delete Node)
Khi bạn muốn thanh lý vĩnh viễn máy chủ đó:

**1. Xóa đối tượng Node trong Kubernetes API:**
```bash
kubectl delete node <node-name>
```

*Ví dụ:*
```bash
kubectl delete node node-worker-01
```
```text
node "node-worker-01" deleted
```

**2. Dọn dẹp vệ sinh trên chính máy chủ Worker (Node Cleanup):**
Việc xóa trên `kubectl` mới chỉ là xóa bản ghi trong `etcd` của Control Plane. Trên máy chủ worker, tiến trình `kubelet` và container runtime vẫn có thể đang chạy ngầm. Hãy SSH vào máy chủ đó và thực hiện dọn dẹp:

- **Nếu dùng RKE2:**
  ```bash
  sudo rke2-killall.sh
  sudo rke2-uninstall.sh
  ```
- **Nếu dùng Kubeadm:**
  ```bash
  sudo kubeadm reset -f
  sudo rm -rf /etc/cni/net.d
  sudo iptables -F && sudo iptables -t nat -F
  ```
- **Nếu dùng K3s:**
  ```bash
  /usr/local/bin/k3s-agent-uninstall.sh
  ```

---

## 📋 5. Bảng Tra Cứu Nhanh (Cheatsheet Vận Hành Node)

| Thao tác | Câu lệnh `kubectl` thực tế |
| :--- | :--- |
| **Xem nhãn tất cả Node** | `kubectl get nodes --show-labels` |
| **Xem nhãn dưới dạng cột** | `kubectl get nodes -L <label-1>,<label-2>` |
| **Gán nhãn mới cho Node** | `kubectl label node <node-name> <key>=<value>` |
| **Ghi đè nhãn đã có** | `kubectl label node --overwrite <node-name> <key>=<value>` |
| **Xóa nhãn khỏi Node** | `kubectl label node <node-name> <key>-` |
| **Xem Taints của tất cả Node** | `kubectl get nodes -o custom-columns=NAME:.metadata.name,TAINTS:.spec.taints --no-headers` |
| **Gán Taint cho Node** | `kubectl taint nodes <node-name> <key>=<value>:<effect>` |
| **Xóa Taint trên 1 Node** | `kubectl taint nodes <node-name> <key>:<effect>-` (hoặc `<key>-`) |
| **Xóa Taint trên TOÀN BỘ Node** | `kubectl taint nodes --all <key>-` |
| **Phong tỏa Node (Khóa)** | `kubectl cordon <node-name>` |
| **Mở phong tỏa Node** | `kubectl uncordon <node-name>` |
| **Di tản Pods (Drain chuẩn)** | `kubectl drain <node-name> --delete-emptydir-data --ignore-daemonsets --force` |
| **Xóa Node khỏi Cluster** | `kubectl delete node <node-name>` |

---

## ⚠️ 6. Những cạm bẫy "xương máu" cần tránh khi vận hành Node

1. **Lệnh Drain bị kẹt vô tận (Hung Drain) do `PodDisruptionBudget` (PDB):**
   - Nhiều đội ngũ DevOps thiết lập PDB quá khắt khe (ví dụ: `minAvailable: 100%` hoặc `maxUnavailable: 0`). Khi bạn chạy `drain`, Eviction API sẽ tôn trọng PDB và từ chối tắt Pod cũ nếu Pod mới chưa sẵn sàng. Nếu cluster không còn đủ node nào khác để đặt Pod mới, lệnh `drain` sẽ đứng chờ vĩnh viễn!
   - *Cách xử lý:* Kiểm tra `kubectl get pdb -A` trước khi drain để điều chỉnh ngân sách gián đoạn hợp lý.

2. **Downtime do ứng dụng chỉ có `replicas: 1`:**
   - Dù bạn có dùng lệnh `drain` chuẩn chỉ đến đâu, nếu ứng dụng của bạn chỉ cấu hình chạy đúng 1 Pod (`replicas: 1`), thì khi Pod đó bị evict và tạo lại ở node mới, chắc chắn sẽ có khoảng thời gian chết (downtime từ vài giây đến vài chục giây).
   - *Khuyến cáo:* Các dịch vụ quan trọng phục vụ khách hàng trên Production luôn phải có ít nhất `replicas: 2` kèm theo cấu hình `readinessProbe` chuẩn xác.

3. **Lạm dụng Taints gây phân mảnh tài nguyên (Resource Fragmentation):**
   - Đặt quá nhiều Taints chuyên biệt trên từng node sẽ khiến `kube-scheduler` gặp bế tắc khi xếp lịch. Hệ quả là có những máy chủ CPU/RAM chạy tới 95% trong khi các máy chủ khác lại ngồi chơi với mức sử dụng chỉ 5% vì không Pod nào có đủ Toleration để vào.

---

## 🎯 Tổng kết

Làm chủ việc quản trị và điều phối Node là bước đệm then chốt trên con đường trở thành một kỹ sư DevOps/SRE chuyên nghiệp:
- **Node Labels** giúp bạn phân loại và điều hướng tài nguyên linh hoạt.
- **Taints & Tolerations** trao cho bạn quyền kiểm soát tối cao để bảo vệ và cách ly các node chuyên biệt.
- **Cordon & Drain** là chiếc "dù cứu sinh" đảm bảo mọi hoạt động bảo dưỡng phần cứng hay nâng cấp hệ điều hành diễn ra trơn tru mà không làm gián đoạn hệ thống.

---

### 🔜 Đón đọc Phần 2: Pod Deep-Dive
Trong bài viết tiếp theo của series **Kubernetes Thực Chiến**, chúng ta sẽ lặn sâu xuống thực thể quan trọng nhất của K8s: **Pod**:
- *Bản chất thực sự của Pod dưới tầng Linux Kernel (Namespaces & Cgroups phối hợp thế nào?)*
- *Phân biệt cặn kẽ 3 loại Probes: Liveness, Readiness, Startup Probe.*
- *Chiến lược thiết lập Resource Requests & Limits để tránh bị OOMKilled.*
- *QoS Classes (Guaranteed, Burstable, BestEffort) và thuật toán trục xuất Pod khi node cạn kiệt tài nguyên.*

Hãy đón chờ nhé! 🚀
