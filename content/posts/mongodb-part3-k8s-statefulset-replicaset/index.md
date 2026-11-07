---
title: "MongoDB Thực Chiến (Phần 3): Triển Khai & Vận Hành MongoDB Replica Set Chuẩn Production Trên Kubernetes"
date: 2026-11-07T16:00:00+07:00
draft: false
description: "Cẩm nang triển khai cụm MongoDB Replica Set 3 node độ sẵn sàng cao (HA) trên Kubernetes: Sử dụng StatefulSet, Headless Service phân giải DNS, phân bổ Pod qua PodAntiAffinity, cấu hình bảo mật liên node bằng Keyfile 400 và script tự động bootstrap cluster."
summary: "Hướng dẫn thực chiến dựng cụm MongoDB Replica Set 3 node chuẩn Production trên K8s: Viết StatefulSet hoàn chỉnh, xử lý quyền hạn Keyfile với Init Container, và tự động hóa quá trình rs.initiate()."
tags: ["MongoDB", "Kubernetes", "K8s", "StatefulSet", "DevOps", "High-Availability", "Cloud-Native"]
categories: ["Kubernetes", "Database", "DevOps"]
series: ["MongoDB Thực Chiến"]
series_order: 3
showTableOfContents: true
---

Việc đưa cơ sở dữ liệu có trạng thái (Stateful) lên Kubernetes từng bị xem là "canh bạc đầy rủi ro". Tuy nhiên, với sự trưởng thành của **StatefulSet**, chuẩn lưu trữ **CSI Driver**, và cơ chế mạng ổn định, việc vận hành một cụm **MongoDB Replica Set** trên Kubernetes đã trở thành giải pháp tối ưu hàng đầu giúp chuẩn hóa hạ tầng đám mây.

Trong bài viết này, chúng ta sẽ cùng xây dựng từng bước một cụm MongoDB Replica Set 3 node chuẩn Production, tự động phục hồi khi có lỗi và bảo mật tuyệt đối.

---

## 1. Tại Sao Bắt Buộc Dùng StatefulSet Thay Vì Deployment?

Trong Kubernetes, đối tượng `Deployment` được thiết kế cho các ứng dụng không trạng thái (Stateless). Các Pod có thể bị hủy diệt và sinh ra ở bất kỳ đâu với tên ngẫu nhiên (ví dụ `api-6d8f89...`). 

Đối với MongoDB Replica Set, điều này là **thảm họa** bởi vì:
1. **Định danh mạng cố định (Predictable DNS):** Các node MongoDB cần biết chính xác địa chỉ của nhau (`mongo-0`, `mongo-1`, `mongo-2`) để duy trì liên lạc và bầu cử.
2. **Gắn kết ổ cứng độc lập (Persistent Identity):** Khi `mongo-1` bị sập và khởi động lại, nó **bắt buộc phải gắn đúng ổ cứng cũ** chứa Oplog và dữ liệu của chính nó, không được phép gắn nhầm ổ cứng của node khác.

{{< mermaid >}}
flowchart TB
    subgraph K8s["☸️ Kubernetes Cluster (Namespace: database)"]
        subgraph HeadlessSVC["🌐 Headless Service: mongo (clusterIP: None)"]
            DNS0["mongo-0.mongo.database.svc.cluster.local"]
            DNS1["mongo-1.mongo.database.svc.cluster.local"]
            DNS2["mongo-2.mongo.database.svc.cluster.local"]
        end

        subgraph StatefulSet["📦 StatefulSet: mongo (replicas: 3)"]
            Pod0["Node 0: mongo-0<br/>(PRIMARY)"]
            Pod1["Node 1: mongo-1<br/>(SECONDARY)"]
            Pod2["Node 2: mongo-2<br/>(SECONDARY)"]
        end

        subgraph Storage["💾 Persistent Volume Claims (volumeClaimTemplates)"]
            PVC0[("PVC: mongo-data-mongo-0<br/>(50Gi)")]
            PVC1[("PVC: mongo-data-mongo-1<br/>(50Gi)")]
            PVC2[("PVC: mongo-data-mongo-2<br/>(50Gi)")]
        end
    end

    DNS0 -.-> Pod0
    DNS1 -.-> Pod1
    DNS2 -.-> Pod2

    Pod0 === PVC0
    Pod1 === PVC1
    Pod2 === PVC2
{{< /mermaid >}}

---

## 2. Kiến Trúc K8s Chuẩn Cho MongoDB Replica Set

Để đảm bảo cụm MongoDB hoạt động tin cậy trong môi trường doanh nghiệp, kiến trúc K8s phải đáp ứng 4 tiêu chuẩn vàng:

1. **Headless Service (`clusterIP: None`):** Cung cấp địa chỉ FQDN cố định cho từng Pod mà không thông qua cơ chế cân bằng tải ngẫu nhiên.
2. **PodAntiAffinity (Chống dồn node):** Ép Kubernetes Scheduler phải phân bổ 3 Pod MongoDB lên **3 Worker Node vật lý khác nhau**. Tránh trường hợp 1 máy chủ vật lý sập làm chết toàn bộ cụm DB.
3. **Internal Authentication (Keyfile):** Các node trong cụm liên lạc với nhau phải được xác thực bằng một mã khóa bí mật chia sẻ (Keyfile với quyền `chmod 400`).
4. **VolumeClaimTemplates:** Mỗi bản sao Pod tự động sinh ra một PVC riêng biệt.

---

## 3. Triển Khai: Toàn Bộ File Manifest (Production-Ready)

Hãy lưu nội dung sau thành file `mongodb-replicaset.yaml`:

```yaml
apiVersion: v1
kind: Namespace
metadata:
  name: database
---
# 1. Secret chứa Keyfile để các node xác thực nội bộ với nhau
apiVersion: v1
kind: Secret
metadata:
  name: mongo-keyfile
  namespace: database
type: Opaque
stringData:
  mongo-key.txt: "ProductionSuperSecretKeyfile2026SecureHashStringABCXYZ123!"
---
# 2. Headless Service tạo bản ghi DNS định danh cho từng Pod
apiVersion: v1
kind: Service
metadata:
  name: mongo
  namespace: database
  labels:
    app: mongo
spec:
  clusterIP: None
  ports:
  - port: 27017
    name: mongodb
  selector:
    app: mongo
---
# 3. Client Service để các ứng dụng nội bộ kết nối vào cụm
apiVersion: v1
kind: Service
metadata:
  name: mongo-client
  namespace: database
  labels:
    app: mongo
spec:
  ports:
  - port: 27017
    targetPort: 27017
  selector:
    app: mongo
---
# 4. StatefulSet chính quản lý 3 node MongoDB
apiVersion: apps/v1
kind: StatefulSet
metadata:
  name: mongo
  namespace: database
spec:
  serviceName: "mongo"
  replicas: 3
  selector:
    matchLabels:
      app: mongo
  template:
    metadata:
      labels:
        app: mongo
    spec:
      affinity:
        # Ngăn chặn 2 Pod MongoDB cùng nằm trên 1 Node vật lý
        podAntiAffinity:
          requiredDuringSchedulingIgnoredDuringExecution:
          - labelSelector:
              matchExpressions:
              - key: app
                operator: In
                values: ["mongo"]
            topologyKey: "kubernetes.io/hostname"
      initContainers:
      # Xử lý quyền hạn cho keyfile: MongoDB bắt buộc file chỉ có quyền đọc cho chủ sở hữu (400)
      - name: setup-keyfile
        image: busybox:1.36
        command:
        - sh
        - -c
        - |
          cp /tmp/raw-key/mongo-key.txt /etc/mongo-key/mongo-key.txt
          chmod 400 /etc/mongo-key/mongo-key.txt
        volumeMounts:
        - name: raw-key
          mountPath: /tmp/raw-key
        - name: secure-key
          mountPath: /etc/mongo-key
      containers:
      - name: mongodb
        image: mongo:7.0
        command:
        - mongod
        - "--replSet=rs0"
        - "--bind_ip_all"
        - "--keyFile=/etc/mongo-key/mongo-key.txt"
        ports:
        - containerPort: 27017
        resources:
          requests:
            cpu: "1000m"
            memory: "2Gi"
          limits:
            cpu: "2000m"
            memory: "4Gi"
        livenessProbe:
          exec:
            command:
            - mongosh
            - --eval
            - "db.adminCommand('ping')"
          initialDelaySeconds: 30
          periodSeconds: 10
          timeoutSeconds: 5
        readinessProbe:
          exec:
            command:
            - mongosh
            - --eval
            - "db.adminCommand('ping')"
          initialDelaySeconds: 15
          periodSeconds: 10
          timeoutSeconds: 5
        volumeMounts:
        - name: mongo-data
          mountPath: /data/db
        - name: secure-key
          mountPath: /etc/mongo-key
          readOnly: true
      volumes:
      - name: raw-key
        secret:
          secretName: mongo-keyfile
      - name: secure-key
        emptyDir: {}
  # Cấp phát ổ cứng tự động cho từng Pod
  volumeClaimTemplates:
  - metadata:
      name: mongo-data
    spec:
      accessModes: [ "ReadWriteOnce" ]
      resources:
        requests:
          storage: 50Gi
```

Áp dụng manifest vào cụm:
```bash
kubectl apply -f mongodb-replicaset.yaml
```

Theo dõi quá trình tạo Pod:
```bash
kubectl get pods -n database -w
```
*Đợi cho đến khi cả 3 Pod: `mongo-0`, `mongo-1`, `mongo-2` đều ở trạng thái `Running`.*

---

## 4. Tự Động Khởi Tạo Cụm (Bootstrap Replica Set)

Khi Pod mới dựng lên lần đầu, chúng là các tiến trình độc lập chưa được kết nối thành một cụm. Ta cần thực thi lệnh `rs.initiate()` thông qua `mongo-0`:

```bash
# Bước 1: Khởi tạo cấu hình Replica Set
kubectl exec -n database mongo-0 -- mongosh --eval '
rs.initiate({
  _id: "rs0",
  members: [
    { _id: 0, host: "mongo-0.mongo.database.svc.cluster.local:27017" },
    { _id: 1, host: "mongo-1.mongo.database.svc.cluster.local:27017" },
    { _id: 2, host: "mongo-2.mongo.database.svc.cluster.local:27017" }
  ]
})
'
```

Chờ 5 giây để cụm tiến hành bầu cử, sau đó tạo tài khoản quản trị tối cao (Root Administrator):

```bash
# Bước 2: Tạo tài khoản Root Admin
kubectl exec -n database mongo-0 -- mongosh --eval '
use admin
db.createUser({
  user: "rootAdmin",
  pwd: "SuperSecureAdminPassword2026!",
  roles: [ { role: "root", db: "admin" } ]
})
'
```

Kiểm tra trạng thái cluster:
```bash
kubectl exec -n database mongo-0 -- mongosh \
  -u rootAdmin -p "SuperSecureAdminPassword2026!" --authenticationDatabase admin \
  --eval 'rs.status().members.forEach(m => print(m.name + " ---> " + m.stateStr))'
```
*Kết quả sẽ hiển thị 1 node `PRIMARY` và 2 node `SECONDARY`.*

---

## 5. Cấu Hình Chuỗi Kết Nối Cho Ứng Dụng (Connection String)

Để ứng dụng của bạn (Spring Boot, Node.js, Golang, Python,...) tự động phát hiện node nào là Primary khi có sự cố failover, chuỗi kết nối chuẩn trong K8s phải liệt kê đầy đủ cả 3 Pod kèm tham số `replicaSet`:

```bash
mongodb://rootAdmin:SuperSecureAdminPassword2026!@mongo-0.mongo.database.svc.cluster.local:27017,mongo-1.mongo.database.svc.cluster.local:27017,mongo-2.mongo.database.svc.cluster.local:27017/my_app_db?replicaSet=rs0&authSource=admin&readPreference=primaryPreferred
```

---

## 6. Thử Nghiệm Tự Động Phục Hồi (Chaos Testing)

Để kiểm chứng độ tin cậy của cụm, hãy thử xóa node `PRIMARY` hiện tại:

```bash
# Xóa cưỡng bức node mongo-0
kubectl delete pod -n database mongo-0
```

Ngay lập tức theo dõi trạng thái từ `mongo-1`:
```bash
kubectl exec -n database mongo-1 -- mongosh \
  -u rootAdmin -p "SuperSecureAdminPassword2026!" --authenticationDatabase admin \
  --eval 'rs.isMaster().primary'
```
Bạn sẽ thấy `mongo-1` hoặc `mongo-2` đã tự động được bầu lên làm **PRIMARY** chỉ trong vòng chưa đầy 3-5 giây! Khi Kubernetes tạo lại Pod `mongo-0`, nó sẽ tự động gia nhập lại cụm với tư cách là một `SECONDARY`.

---

## Tóm tắt & Bước đệm sang Bài 4

* Sử dụng **StatefulSet** kết hợp **Headless Service** là bắt buộc để đảm bảo tính ổn định định danh mạng và dữ liệu cho MongoDB trên K8s.
* **PodAntiAffinity** giúp ngăn ngừa rủi ro mất toàn bộ cluster khi một máy chủ vật lý gặp sự cố.
* Quyền hạn của Keyfile bắt buộc phải là `400`, được giải quyết gọn gàng bằng một `initContainer`.

👉 **Ở bài viết tiếp theo (Bài 4):** Chúng ta sẽ đối mặt với một kịch bản hóc búa thường gặp trong môi trường doanh nghiệp bảo mật cao: **"Cẩm Nang Backup & Restore MongoDB: Từ Môi Trường Thường Đến Kubernetes (Single Node & Replica Set qua phân vùng `/dev/shm`)"**.
