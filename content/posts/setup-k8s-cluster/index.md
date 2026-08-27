---
title: "Hướng dẫn cài đặt Kubernetes HA Cluster (3 Master Nodes RKE2 + Keepalived VIP) cho mọi môi trường (Online & Air-gap)"
date: 2026-08-27T14:48:26+07:00
draft: false
description: "Hướng dẫn chi tiết từng bước thiết lập cụm Kubernetes độ sẵn sàng cao (High Availability) với 3 Master nodes sử dụng RKE2 kết hợp Keepalived Virtual IP (VIP), áp dụng linh hoạt cho cả môi trường Online tiêu chuẩn và môi trường cách ly (Air-gap)."
summary: "Hướng dẫn triển khai cụm Kubernetes HA hoàn chỉnh: 3 Master Nodes với Keepalived VIP, cấu hình node-ip, tls-san, etcd cluster, hỗ trợ cả môi trường Online và Air-gap (offline)."
tags: ["Kubernetes", "RKE2", "High-Availability", "Keepalived", "Air-gap", "DevOps", "Linux"]
categories: ["DevOps", "Kubernetes"]
showTableOfContents: true
---

Khi xây dựng hạ tầng cho môi trường Production (cho dù là On-premise, Cloud, Máy chủ vật lý Bare-metal hay Máy ảo VM), một cụm **Kubernetes độ sẵn sàng cao (High Availability - HA)** là tiêu chuẩn bắt buộc để loại bỏ hoàn toàn rủi ro sập hệ thống (Single Point of Failure - SPOF).

Bài viết này sẽ hướng dẫn bạn chi tiết từng bước xây dựng cụm **Kubernetes HA với 3 Master Nodes** sử dụng **RKE2 (Rancher Kubernetes Engine 2)** kết hợp **Keepalived Virtual IP (VIP)**. Hướng dẫn được thiết kế để áp dụng cho:
- 🌐 **Môi trường Online tiêu chuẩn:** Các máy chủ có kết nối Internet bình thường.
- 🔒 **Môi trường Air-gap / Offline:** Môi trường cô lập bảo mật, không có kết nối Internet trực tiếp.

---

## 🏗️ Mô hình kiến trúc mẫu (Cluster Topology)

| Vai trò | Hostname | IP vật lý | Ghi chú |
| :--- | :--- | :--- | :--- |
| **Virtual IP (VIP)** | `k8s-vip.local` | `192.168.1.10` | Do Keepalived quản lý để cân bằng tải / failover |
| **Master Node 1** | `node-master-01` | `192.168.1.11` | Server 1 (Init Cluster / etcd member 1) |
| **Master Node 2** | `node-master-02` | `192.168.1.12` | Server 2 (Join HA / etcd member 2) |
| **Master Node 3** | `node-master-03` | `192.168.1.13` | Server 3 (Join HA / etcd member 3) |
| **Worker Node 1** | `node-worker-01` | `192.168.1.21` | Agent 1 (Chạy workload ứng dụng) |
| **Worker Node 2** | `node-worker-02` | `192.168.1.22` | Agent 2 (Chạy workload ứng dụng) |

> 📌 **Tài liệu tham khảo chính thức:**
> - [Yêu cầu hệ thống RKE2 (RKE2 Requirements)](https://docs.rke2.io/install/requirements)
> - [RKE2 High Availability Architecture](https://docs.rke2.io/install/ha)
> - [Hướng dẫn cài đặt Air-gap RKE2](https://docs.rke2.io/install/airgap)

---

## I. Chuẩn bị hệ thống (Prerequisites)

> ⚠️ **Lưu ý:** Các bước 1, 2 thực hiện trên **tất cả các node** (cả Master và Worker). Bước 3 thực hiện trên **3 Master nodes**.

### 1. Cấu hình Mạng & NetworkManager

#### 1.1. Cấu hình NetworkManager
Nếu hệ điều hành của node đang bật `NetworkManager`, cần cấu hình để nó **bỏ qua các interface do CNI quản lý** (Calico/Flannel) nhằm tránh xung đột bảng định tuyến:

```bash
# Tạo file cấu hình bỏ qua interface CNI
mkdir -p /etc/NetworkManager/conf.d
cat << 'EOF' > /etc/NetworkManager/conf.d/rke2-canal.conf
[keyfile]
unmanaged-devices=interface-name:cali*;interface-name:flannel*
EOF

# Tải lại NetworkManager
systemctl reload NetworkManager
```

#### 1.2. Cấu hình IP tĩnh cố định (Netplan)
Đảm bảo tất cả các node có IP tĩnh cố định trong mạng nội bộ:

```yaml
# /etc/netplan/k8s-config.yaml
network:
  version: 2
  ethernets:
    ens3: # Đổi thành tên card mạng thực tế (vd: ens3, eth0)
      addresses:
        - 192.168.1.11/24 # Thay đổi theo IP từng node
      routes:
        - to: default
          via: 192.168.1.1
      nameservers:
        addresses:
          - 192.168.1.2
        search: []
```

Áp dụng cấu hình mạng:
```bash
netplan apply
```

---

### 2. Cấu hình Phân giải tên miền (`/etc/hosts`)

Khai báo phân giải tên miền nội bộ trên **toàn bộ các node** trong file `/etc/hosts`:

```bash
# /etc/hosts
192.168.1.10   k8s-vip.local
192.168.1.11   node-master-01.local node-master-01
192.168.1.12   node-master-02.local node-master-02
192.168.1.13   node-master-03.local node-master-03
192.168.1.21   node-worker-01.local node-worker-01
192.168.1.22   node-worker-02.local node-worker-02
```

---

### 3. Cài đặt và Cấu hình Keepalived Virtual IP (VIP) trên 3 Node Master

Keepalived sử dụng giao thức **VRRP** để cấp phát IP ảo (`192.168.1.10`) làm điểm truy cập chung cho toàn bộ Master. Khi Master đang giữ VIP gặp sự cố, VIP sẽ tự động chuyển sang Master tiếp theo.

#### 3.1. Cài đặt Keepalived
- **Môi trường Online (Có Internet):**
  ```bash
  # Ubuntu/Debian:
  apt-get update && apt-get install -y keepalived
  # RHEL/CentOS/Rocky Linux:
  yum install -y keepalived
  ```
- **Môi trường Air-gap (Offline):** Tải sẵn các file `.deb` hoặc `.rpm` của gói `keepalived` và cài đặt thủ công bằng `dpkg -i` hoặc `rpm -ivh`.

#### 3.2. Tạo script kiểm tra sức khỏe RKE2 API Server
Tạo file `/etc/keepalived/check_apiserver.sh` trên **cả 3 node Master**:

```bash
cat << 'EOF' > /etc/keepalived/check_apiserver.sh
#!/bin/bash
APISERVER_PORT="6443"

if curl --silent --insecure https://127.0.0.1:${APISERVER_PORT}/healthz | grep -q "ok"; then
    exit 0
else
    # Nếu API Server chưa sẵn sàng trong lúc khởi động, kiểm tra cổng 9345
    if nc -z 127.0.0.1 9345; then
        exit 0
    fi
    exit 1
fi
EOF

chmod +x /etc/keepalived/check_apiserver.sh
```

#### 3.3. Cấu hình `/etc/keepalived/keepalived.conf` trên từng Master

##### Cấu hình trên **Master Node 1** (`192.168.1.11`):
```ini
global_defs {
    router_id k8s_master_01
    enable_script_security
    script_user root
}

vrrp_script check_apiserver {
    script "/etc/keepalived/check_apiserver.sh"
    interval 3
    weight -20
    fall 2
    rise 2
}

vrrp_instance VI_K8S {
    state MASTER
    interface ens3              # Card mạng thực tế của máy
    virtual_router_id 51
    priority 101               # Độ ưu tiên cao nhất
    advert_int 1

    authentication {
        auth_type PASS
        auth_pass K8sSecretPass123
    }

    virtual_ipaddress {
        192.168.1.10/24 dev ens3
    }

    track_script {
        check_apiserver
    }
}
```

##### Cấu hình trên **Master Node 2** (`192.168.1.12`):
```ini
global_defs {
    router_id k8s_master_02
    enable_script_security
    script_user root
}

vrrp_script check_apiserver {
    script "/etc/keepalived/check_apiserver.sh"
    interval 3
    weight -20
    fall 2
    rise 2
}

vrrp_instance VI_K8S {
    state BACKUP
    interface ens3
    virtual_router_id 51
    priority 100               # Độ ưu tiên thứ 2
    advert_int 1

    authentication {
        auth_type PASS
        auth_pass K8sSecretPass123
    }

    virtual_ipaddress {
        192.168.1.10/24 dev ens3
    }

    track_script {
        check_apiserver
    }
}
```

##### Cấu hình trên **Master Node 3** (`192.168.1.13`):
```ini
global_defs {
    router_id k8s_master_03
    enable_script_security
    script_user root
}

vrrp_script check_apiserver {
    script "/etc/keepalived/check_apiserver.sh"
    interval 3
    weight -20
    fall 2
    rise 2
}

vrrp_instance VI_K8S {
    state BACKUP
    interface ens3
    virtual_router_id 51
    priority 99                # Độ ưu tiên thứ 3
    advert_int 1

    authentication {
        auth_type PASS
        auth_pass K8sSecretPass123
    }

    virtual_ipaddress {
        192.168.1.10/24 dev ens3
    }

    track_script {
        check_apiserver
    }
}
```

#### 3.4. Khởi chạy Keepalived
Chạy trên cả 3 Master:
```bash
systemctl enable --now keepalived
```

Kiểm tra VIP đã xuất hiện trên Master 1:
```bash
ip a show ens3
```

---

## II. Cài đặt RKE2 Binary (Chọn theo môi trường)

Thực hiện bước cài đặt RKE2 binary trên **tất cả các node** (cả Master và Worker).

### Lựa chọn A: Môi trường Online (Có kết nối Internet)
Chỉ cần chạy lệnh cài đặt chính thức của RKE2:

```bash
# Cài đặt phiên bản RKE2 mới nhất (hoặc chỉ định INSTALL_RKE2_VERSION=v1.29.4+rke2r1)
curl -sfL https://get.rke2.io | sh -
```

---

### Lựa chọn B: Môi trường Air-gap (Offline hoàn toàn)

Trong môi trường không có Internet, bạn cần chuẩn bị các gói artifact từ một máy có mạng:

1. **Tải các gói cần thiết từ [RKE2 Releases](https://github.com/rancher/rke2/releases):**
   ```bash
   # 1. Images bundle
   wget https://github.com/rancher/rke2/releases/download/v1.29.4+rke2r1/rke2-images.linux-amd64.tar.zst
   # 2. RKE2 binary
   wget https://github.com/rancher/rke2/releases/download/v1.29.4+rke2r1/rke2.linux-amd64.tar.gz
   # 3. Checksum
   wget https://github.com/rancher/rke2/releases/download/v1.29.4+rke2r1/sha256sum-amd64.txt
   # 4. Script cài đặt
   curl -sfL https://get.rke2.io --output install.sh
   ```

2. **Chuyển vào thư mục artifacts trên từng node:**
   ```bash
   mkdir -p /root/rke2-artifacts
   mv install.sh rke2-images.linux-amd64.tar.zst rke2.linux-amd64.tar.gz sha256sum-amd64.txt /root/rke2-artifacts/
   ```

3. **Chạy script cài đặt offline:**
   ```bash
   INSTALL_RKE2_ARTIFACT_PATH=/root/rke2-artifacts sh /root/rke2-artifacts/install.sh
   ```

---

## III. Cấu hình và Khởi tạo Cụm K8s High Availability

### 1. Giải thích các tham số quan trọng trong `config.yaml`

Trước khi cấu hình, hãy nắm rõ các tham số cốt lõi sau:

- **`node-name`:** Tên định danh của node trong cluster (thường đặt trùng hostname).
- **`node-ip`:** Địa chỉ IP vật lý của node.
  - ⚠️ **Tại sao `node-ip` bắt buộc khi dùng Keepalived?** Khi Keepalived chạy, trên card mạng sẽ có thêm IP ảo VIP (`192.168.1.10`). Nếu không khai báo `node-ip`, Kubelet có thể tự động nhận nhầm VIP làm Node IP thay vì IP vật lý (`192.168.1.11`), gây lỗi định tuyến và CNI khi VIP failover.
- **`tls-san`:** Danh sách các IP và domain được thêm vào chứng chỉ SSL của API Server (port `6443`) và Supervisor (port `9345`). Cần khai báo: **VIP (`192.168.1.10`)**, **IP của cả 3 node Master**, và **Hostname tương ứng**.
  - ⚠️ **`tls-san` chỉ cần cấu hình trên Master node**, Worker node **không cần**.
- **`token`:** Chuỗi mật mã bí mật dùng chung cho toàn bộ cụm để các node join vào.

---

### 2. Cấu hình & Khởi chạy 3 Node Master

#### Bước 2.1: Master Node 1 (Bootstrap Master)
Tạo file `/etc/rancher/rke2/config.yaml` trên **Master 1**:

```bash
mkdir -p /etc/rancher/rke2
```

```yaml
# /etc/rancher/rke2/config.yaml trên Master 1
node-name: node-master-01
node-ip: "192.168.1.11"           # IP vật lý của Master 1
token: "my-super-secure-ha-token" # Token bảo mật dùng chung cho toàn cụm
tls-san:
  - "192.168.1.10"                # Địa chỉ VIP
  - "k8s-vip.local"               # Domain của VIP
  - "192.168.1.11"                # IP Master 1
  - "192.168.1.12"                # IP Master 2
  - "192.168.1.13"                # IP Master 3
  - "node-master-01.local"
  - "node-master-02.local"
  - "node-master-03.local"
debug: true
cni:
  - canal
disable-cloud-controller: true
enable-servicelb: true
kube-apiserver-arg:
  - "default-not-ready-toleration-seconds=30"
  - "default-unreachable-toleration-seconds=30"
```

Khởi động Master 1:
```bash
systemctl enable --now rke2-server
```

Theo dõi log khởi động:
```bash
journalctl -u rke2-server -f
```

---

#### Bước 2.2: Master Node 2 & Master Node 3 (Join vào etcd HA)

Khi Master 1 đã hoạt động, cấu hình Master 2 và Master 3 để kết nạp vào cụm etcd đa điểm bằng cách thêm tham số `server: https://192.168.1.10:9345` (trỏ qua VIP).

##### Cấu hình trên **Master Node 2**:
Tạo file `/etc/rancher/rke2/config.yaml`:
```yaml
# /etc/rancher/rke2/config.yaml trên Master 2
server: https://192.168.1.10:9345 # Trỏ tới VIP (hoặc https://192.168.1.11:9345)
node-name: node-master-02
node-ip: "192.168.1.12"           # IP vật lý của Master 2
token: "my-super-secure-ha-token"
tls-san:
  - "192.168.1.10"
  - "k8s-vip.local"
  - "192.168.1.11"
  - "192.168.1.12"
  - "192.168.1.13"
  - "node-master-01.local"
  - "node-master-02.local"
  - "node-master-03.local"
debug: true
cni:
  - canal
disable-cloud-controller: true
enable-servicelb: true
kube-apiserver-arg:
  - "default-not-ready-toleration-seconds=30"
  - "default-unreachable-toleration-seconds=30"
```

Khởi động Master 2:
```bash
systemctl enable --now rke2-server
```

##### Cấu hình trên **Master Node 3**:
Tạo file `/etc/rancher/rke2/config.yaml`:
```yaml
# /etc/rancher/rke2/config.yaml trên Master 3
server: https://192.168.1.10:9345 # Trỏ tới VIP
node-name: node-master-03
node-ip: "192.168.1.13"           # IP vật lý của Master 3
token: "my-super-secure-ha-token"
tls-san:
  - "192.168.1.10"
  - "k8s-vip.local"
  - "192.168.1.11"
  - "192.168.1.12"
  - "192.168.1.13"
  - "node-master-01.local"
  - "node-master-02.local"
  - "node-master-03.local"
debug: true
cni:
  - canal
disable-cloud-controller: true
enable-servicelb: true
kube-apiserver-arg:
  - "default-not-ready-toleration-seconds=30"
  - "default-unreachable-toleration-seconds=30"
```

Khởi động Master 3:
```bash
systemctl enable --now rke2-server
```

> ⚠️ **Lưu ý:** Hãy khởi động **Master 2** trước, theo dõi log hoàn tất rồi mới khởi động **Master 3** để cụm etcd kết nạp an toàn, tránh xung đột quorum.

---

### 3. Cấu hình & Khởi chạy các Worker Nodes (Agents)

Trên các node Worker, cấu hình trỏ trực tiếp tới **VIP** để đảm bảo khả năng tự động failover nếu bất kỳ Master node nào gặp sự cố.

#### Cấu hình trên **Worker Node 1**:
Tạo file `/etc/rancher/rke2/config.yaml`:
```yaml
# /etc/rancher/rke2/config.yaml trên Worker 1
server: https://192.168.1.10:9345 # Trỏ trực tiếp vào VIP
node-name: node-worker-01
node-ip: "192.168.1.21"           # IP vật lý của Worker 1
token: "my-super-secure-ha-token"
debug: true
cni:
  - canal
disable-cloud-controller: true
enable-servicelb: true
kube-apiserver-arg:
  - "default-not-ready-toleration-seconds=30"
  - "default-unreachable-toleration-seconds=30"
```

Khởi động Agent trên Worker 1:
```bash
systemctl enable --now rke2-agent
```

*(Thực hiện tương tự cho `node-worker-02` với `node-name: node-worker-02` và `node-ip: "192.168.1.22"`)*.

---

### 4. Cấu hình CLI `kubectl` trỏ qua VIP

Thiết lập `kubectl` trên bất kỳ node Master nào hoặc trên máy quản trị từ xa:

```bash
# 1. Tạo symlink cho kubectl
ln -s /var/lib/rancher/rke2/bin/kubectl /usr/local/bin/kubectl

# 2. Cấu hình KUBECONFIG
mkdir -p ~/.kube
cp /etc/rancher/rke2/rke2.yaml ~/.kube/config
chmod 600 ~/.kube/config

# 3. Đổi địa chỉ server trong kubeconfig sang VIP
sed -i 's/127.0.0.1/192.168.1.10/g' ~/.kube/config

# 4. Kiểm tra trạng thái toàn bộ cụm HA
kubectl get nodes -o wide
```

Kết quả hiển thị:
```text
NAME             STATUS   ROLES                       AGE     VERSION          INTERNAL-IP
node-master-01   Ready    control-plane,etcd,master   20m     v1.29.4+rke2r1   192.168.1.11
node-master-02   Ready    control-plane,etcd,master   15m     v1.29.4+rke2r1   192.168.1.12
node-master-03   Ready    control-plane,etcd,master   10m     v1.29.4+rke2r1   192.168.1.13
node-worker-01   Ready    <none>                      5m      v1.29.4+rke2r1   192.168.1.21
node-worker-02   Ready    <none>                      3m      v1.29.4+rke2r1   192.168.1.22
```

---

## IV. Hướng dẫn chuyển đổi Master Node thành Worker Node

Nếu trong quá trình vận hành, bạn muốn hạ cấp một Master node thành Worker node:

### Bước 1: Drain node trên Cluster
```bash
kubectl drain <ten-node> --ignore-daemonsets --delete-emptydir-data
```

### Bước 2: Dừng và vô hiệu hóa `rke2-server` trên node đó
```bash
systemctl stop rke2-server
systemctl disable rke2-server
```

### Bước 3: Xóa node khỏi cụm etcd và cluster
Chạy lệnh này từ một Master node còn sống:
```bash
kubectl delete node <ten-node>
```

### Bước 4: Sửa file cấu hình và khởi động `rke2-agent`
1. Đảm bảo `/etc/rancher/rke2/config.yaml` đã có `server: https://192.168.1.10:9345`, `node-ip`, và `token`.
2. Khởi động service agent:
   ```bash
   systemctl enable --now rke2-agent
   ```

### Bước 5: Mở khóa node (Uncordon)
```bash
kubectl uncordon <ten-node>
kubectl get nodes
```

---

## 🎯 Tổng kết

Mô hình **3 Master Nodes kết hợp Keepalived VIP** là kiến trúc chuẩn cho Kubernetes Production:
1. **Khả năng chịu lỗi cao:** Cụm etcd 3 node duy trì quorum an toàn nếu 1 node chết; Keepalived chuyển đổi VIP tức thì sang Master khả dụng.
2. **Linh hoạt triển khai:** Áp dụng đồng nhất cho cả môi trường Online tốc độ cao và môi trường Air-gap cách ly nghiêm ngặt.
3. **Quản trị dễ dàng:** Mọi thao tác `kubectl` và kết nối Worker đều đi qua một đầu mối VIP duy nhất.
