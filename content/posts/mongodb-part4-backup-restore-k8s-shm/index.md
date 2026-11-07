---
title: "MongoDB Thực Chiến (Phần 4): Cẩm Nang Backup & Restore – Từ Môi Trường Thường Đến Kubernetes (Single Node, Replica Set & Giải Pháp /dev/shm)"
date: 2026-11-07T17:00:00+07:00
draft: false
description: "Cẩm nang toàn diện về sao lưu và phục hồi dữ liệu trong MongoDB: Phân biệt mongodump vs mongoexport. Xử lý kịch bản sao lưu trong môi trường Kubernetes với ràng buộc bảo mật khắt khe: Dữ liệu chỉ có thể copy qua thư mục bộ nhớ chia sẻ /dev/shm. Hướng dẫn chi tiết cho cả 2 chế độ Single Node và Replica Set (Primary vs Secondary), kèm kỹ thuật streaming vượt qua giới hạn dung lượng RAM."
summary: "Hướng dẫn thực chiến Backup & Restore MongoDB: Từ Bare-metal đến Kubernetes Pods bị giới hạn chỉ ghi được vào /dev/shm. Xử lý triệt để cho cả 2 mô hình Single Node và Replica Set, cùng script tự động hóa an toàn tránh OOM."
tags: ["MongoDB", "Backup", "Restore", "Kubernetes", "K8s", "DevOps", "Database", "mongodump"]
categories: ["Kubernetes", "Database", "DevOps"]
series: ["MongoDB Thực Chiến"]
series_order: 4
showTableOfContents: true
---

Sao lưu (Backup) và Phục hồi (Restore) dữ liệu là "tấm lưới bảo hiểm" sống còn của bất kỳ hệ thống dữ liệu nào. Nếu việc backup trên máy chủ truyền thống (Bare-metal hoặc Virtual Machine) tương đối thẳng thắn, thì khi bước vào thế giới **Kubernetes**, bạn sẽ gặp phải những rào cản chính sách bảo mật khắt khe: Container non-root, hệ thống tệp gốc ở chế độ chỉ đọc (`readOnlyRootFilesystem: true`), và **dữ liệu chỉ được phép sao chép tạm thời vào phân vùng bộ nhớ chia sẻ `/dev/shm`**.

Bài viết này sẽ hướng dẫn bạn toàn diện từ việc phân biệt công cụ, thực hiện trên môi trường thường, cho đến xử lý trọn vẹn 2 chế độ chạy trên Kubernetes (**Đơn Node - Single Node** và **Cụm HA - Replica Set**) thông qua địa chỉ `/dev/shm`.

---

## 1. Phân biệt công cụ: `mongodump/mongorestore` vs `mongoexport/mongoimport`

Trước khi bắt tay vào thực hành, cần phân biệt rõ 2 bộ công cụ có sẵn trong gói MongoDB Database Tools:

| Tiêu chí so sánh | `mongodump` / `mongorestore` | `mongoexport` / `mongoimport` |
| :--- | :--- | :--- |
| **Định dạng dữ liệu** | **Binary (BSON)** + Metadata JSON | **Plain Text (JSON hoặc CSV)** |
| **Bảo toàn kiểu dữ liệu** | **100% chính xác** (ObjectId, Date, NumberLong, Binary,...) | Dễ mất mát định dạng chặt chẽ của BSON |
| **Chỉ số (Indexes)** | **Tự động lưu và khôi phục toàn bộ Index** | **Không** lưu Index (phải tạo lại bằng tay) |
| **Tốc độ xử lý** | Nhanh hơn gấp nhiều lần (xử lý nhị phân) | Chậm hơn (tốn CPU parse text/json) |
| **Mục đích sử dụng** | **Backup & Restore hệ thống (Disaster Recovery)** | Export báo cáo, ETL sang Data Warehouse |

> 💡 **Quy tắc vàng:** Cho mục đích sao lưu thảm họa và phục hồi dữ liệu hệ thống, **luôn luôn dùng `mongodump` và `mongorestore`**.

---

## 2. Trường hợp 1: Chạy bình thường (Bare Metal / VM / Docker Host)

### 2.1. Backup dữ liệu (`mongodump`)
Thay vì dump ra hàng ngàn thư mục và file `.bson` rời rạc, hãy sử dụng 2 cờ `--archive` và `--gzip` để nén toàn bộ thành **một file duy nhất**:

```bash
mongodump \
  --uri="mongodb://admin:SecurePass123@127.0.0.1:27017/?authSource=admin" \
  --db=ecommerce_db \
  --archive=/var/backups/mongodb/ecommerce_$(date +%F).dump.gz \
  --gzip
```

### 2.2. Restore dữ liệu (`mongorestore`)
Khi phục hồi, thêm cờ `--drop` để xóa sạch collection cũ trước khi nạp dữ liệu mới, tránh xung đột khóa chính:

```bash
mongorestore \
  --uri="mongodb://admin:SecurePass123@127.0.0.1:27017/?authSource=admin" \
  --nsInclude="ecommerce_db.*" \
  --archive=/var/backups/mongodb/ecommerce_2026-11-07.dump.gz \
  --gzip \
  --drop
```

---

## 3. Thách Thức Khi Chạy Trên Kubernetes: Ràng Buộc `/dev/shm`

Trong các cụm Kubernetes đạt chuẩn bảo mật ngân hàng / doanh nghiệp lớn:
* Container chạy dưới user non-root (`runAsNonRoot: true`).
* Root Filesystem bị khóa chỉ đọc (`readOnlyRootFilesystem: true`).
* Thư mục duy nhất có quyền ghi tạm mà không cần mount thêm đĩa cứng chuyên dụng là **/dev/shm** (POSIX Shared Memory).

{{< mermaid >}}
flowchart LR
    subgraph Host["💻 Máy Quản Trị / Bastion Host"]
        BackupFile["📁 file_backup.dump.gz"]
    end

    subgraph Pod["☸️ MongoDB Pod trong Kubernetes"]
        subgraph Mem["💾 RAM (tmpfs)"]
            SHM["📂 /dev/shm/<br/>(Bộ nhớ chia sẻ)"]
        end
        Engine["⚙️ MongoDB Process (mongod)"]
    end

    BackupFile <-->|kubectl cp| SHM
    SHM <-->|mongodump / mongorestore| Engine
{{< /mermaid >}}

### ⚠️ Lưu ý sống còn về `/dev/shm`:
1. **Dung lượng mặc định:** Thường là **64MB** trên Linux container trừ khi Pod spec có mount `emptyDir: { medium: Memory }`.
2. **Nguy cơ OOMKilled:** Dữ liệu trong `/dev/shm` nằm trực tiếp trên RAM. Nếu bạn dump file lớn vào đây mà quên xóa, Pod sẽ bị tràn RAM và Kubernetes sẽ tiêu diệt Pod ngay lập tức (`OOMKilled`)! **Bắt buộc phải xóa file ngay sau khi xử lý xong.**

---

## 4. Kubernetes Chế Độ 1: Chạy Đơn Node (Single Node)

Mô hình này thường áp dụng cho môi trường Staging/Dev hoặc các dịch vụ phụ trợ chạy 1 Pod độc lập (ví dụ Pod: `mongodb-single-0`).

### 4.1. Quy trình Backup từ Single Node Pod:
```bash
NAMESPACE="database"
POD_NAME="mongodb-single-0"
BACKUP_FILE="backup_single_$(date +%Y%m%d_%H%M%S).dump.gz"

# Bước 1: Dump trực tiếp vào /dev/shm của Pod
kubectl exec -n $NAMESPACE $POD_NAME -- \
  mongodump \
    --username="admin" \
    --password="SecurePass123" \
    --authenticationDatabase="admin" \
    --db="ecommerce_db" \
    --archive=/dev/shm/$BACKUP_FILE \
    --gzip

# Bước 2: Kéo file từ /dev/shm của Pod về máy quản trị
kubectl cp $NAMESPACE/$POD_NAME:/dev/shm/$BACKUP_FILE ./$BACKUP_FILE

# Bước 3: DỌN DẸP NGAY LẬP TỨC để giải phóng RAM của Pod
kubectl exec -n $NAMESPACE $POD_NAME -- rm -f /dev/shm/$BACKUP_FILE
```

### 4.2. Quy trình Restore vào Single Node Pod:
```bash
LOCAL_FILE="./backup_single_20261107.dump.gz"
REMOTE_DEST="/dev/shm/restore_temp.dump.gz"

# Bước 1: Đẩy file backup vào /dev/shm của Pod
kubectl cp $LOCAL_FILE $NAMESPACE/$POD_NAME:$REMOTE_DEST

# Bước 2: Thực thi lệnh mongorestore đọc từ /dev/shm
kubectl exec -n $NAMESPACE $POD_NAME -- \
  mongorestore \
    --username="admin" \
    --password="SecurePass123" \
    --authenticationDatabase="admin" \
    --nsInclude="ecommerce_db.*" \
    --archive=$REMOTE_DEST \
    --gzip \
    --drop

# Bước 3: Xóa file tạm trong /dev/shm
kubectl exec -n $NAMESPACE $POD_NAME -- rm -f $REMOTE_DEST
```

---

## 5. Kubernetes Chế Độ 2: Chạy Replica Set (Cụm HA)

Khi chạy cụm Replica Set (ví dụ gồm 3 pod: `mongo-0`, `mongo-1`, `mongo-2`):

{{< mermaid >}}
flowchart TD
    subgraph RS["☸️ MongoDB Replica Set"]
        Primary["⭐ PRIMARY NODE<br/>(mongo-0)<br/>Quyền: READ & WRITE"]
        Secondary["🔄 SECONDARY NODE<br/>(mongo-1 / mongo-2)<br/>Quyền: READ ONLY"]
    end

    BackupAction["📥 BACKUP TÁC VỤ"] -.->|Nên dump từ| Secondary
    RestoreAction["📤 RESTORE TÁC VỤ"] ==>|BẮT BUỘC ghi vào| Primary
    Primary -->|Tự động sync qua Oplog| Secondary
{{< /mermaid >}}

* 📥 **Nguyên tắc Backup:** Nên thực hiện trên node **SECONDARY** để tránh chiếm dụng CPU và I/O của node Primary đang phục vụ khách hàng. Thêm cờ `--oplog` để đảm bảo tính nhất quán giao dịch tại một thời điểm (Point-in-time consistency).
* 📤 **Nguyên tắc Restore:** **BẮT BUỘC PHẢI RESTORE VÀO NODE PRIMARY!** Các node Secondary ở trạng thái chỉ đọc (Read-Only). Mọi dữ liệu nạp vào Primary sẽ tự động được sao chép sang toàn bộ Secondary thông qua Oplog.

---

### 5.1. Backup từ Replica Set (Thực hiện trên Secondary)

Giả sử ta chọn `mongo-1` (Secondary) để backup:

```bash
NAMESPACE="database"
TARGET_POD="mongo-1"
BACKUP_FILE="rs_backup_$(date +%Y%m%d_%H%M%S).dump.gz"

# 1. Dump vào /dev/shm của Secondary Pod kèm cờ --oplog
kubectl exec -n $NAMESPACE $TARGET_POD -- \
  mongodump \
    --username="admin" \
    --password="SecurePass123" \
    --authenticationDatabase="admin" \
    --archive=/dev/shm/$BACKUP_FILE \
    --gzip \
    --oplog

# 2. Copy file về máy local
kubectl cp $NAMESPACE/$TARGET_POD:/dev/shm/$BACKUP_FILE ./$BACKUP_FILE

# 3. Dọn dẹp /dev/shm trên Pod
kubectl exec -n $NAMESPACE $TARGET_POD -- rm -f /dev/shm/$BACKUP_FILE
```

---

### 5.2. Restore vào Replica Set (Tự động phát hiện và ghi vào Primary)

Trước khi restore, ta cần một câu lệnh tự động kiểm tra xem pod nào đang nắm quyền Primary (vì Primary có thể thay đổi sau bầu cử):

```bash
NAMESPACE="database"
BACKUP_FILE="./rs_backup_20261107.dump.gz"
REMOTE_PATH="/dev/shm/rs_restore.dump.gz"

# 1. Tự động truy vấn tìm tên Pod đang là PRIMARY
PRIMARY_POD=$(kubectl exec -n $NAMESPACE mongo-0 -- mongosh --quiet \
  -u "admin" -p "SecurePass123" --authenticationDatabase "admin" \
  --eval "db.isMaster().primary.split(':')[0].split('.')[0]")

echo "===> Node Primary hiện tại là: $PRIMARY_POD"

# 2. Copy file backup vào /dev/shm của PRIMARY Pod
kubectl cp $BACKUP_FILE $NAMESPACE/$PRIMARY_POD:$REMOTE_PATH

# 3. Thực thi mongorestore kèm cờ --oplogReplay
kubectl exec -n $NAMESPACE $PRIMARY_POD -- \
  mongorestore \
    --username="admin" \
    --password="SecurePass123" \
    --authenticationDatabase="admin" \
    --archive=$REMOTE_PATH \
    --gzip \
    --oplogReplay \
    --drop

# 4. Xóa file khỏi /dev/shm của Primary
kubectl exec -n $NAMESPACE $PRIMARY_POD -- rm -f $REMOTE_PATH

# 5. Kiểm tra trạng thái đồng bộ của các node Secondary
kubectl exec -n $NAMESPACE $PRIMARY_POD -- mongosh \
  -u "admin" -p "SecurePass123" --authenticationDatabase "admin" \
  --eval "rs.status().members.forEach(m => print(m.name + ' --> ' + m.stateStr))"
```

---

## 6. Pro-Tip: Kỹ Thuật Streaming Pipe (Bypass hoàn toàn giới hạn `/dev/shm`)

Trong thực tế, database của bạn có thể nặng **50GB hoặc 100GB**, trong khi `/dev/shm` chỉ có vài trăm MB RAM. Nếu lưu file tạm vào `/dev/shm`, Pod chắc chắn sẽ chết vì OOM.

Bạn hoàn toàn có thể **bỏ qua việc lưu file vào `/dev/shm`** bằng cách dùng ống dẫn dữ liệu (UNIX Pipe) trực tiếp qua `kubectl exec`:

### Backup dạng Stream (Không tốn 1 byte đĩa hay RAM trong Pod):
```bash
kubectl exec -i -n database mongo-1 -- \
  mongodump \
    --username="admin" \
    --password="SecurePass123" \
    --authenticationDatabase="admin" \
    --archive \
    --gzip > ./backup_direct_stream.dump.gz
```
*(Dữ liệu từ stdout của container được bắn thẳng qua đường truyền mạng và ghi trực tiếp vào ổ cứng máy host).*

### Restore dạng Stream (Đẩy trực tiếp từ máy host vào Pod):
```bash
cat ./backup_direct_stream.dump.gz | kubectl exec -i -n database $PRIMARY_POD -- \
  mongorestore \
    --username="admin" \
    --password="SecurePass123" \
    --authenticationDatabase="admin" \
    --archive \
    --gzip \
    --drop
```

---

## 7. Script Tự Động Hóa Sản Xuất (Có Trap Cleanup An Toàn)

Hãy lưu script này lại để dùng cho các tác vụ CronJob hoặc pipeline CI/CD:

```bash
#!/usr/bin/env bash
set -eo pipefail

NAMESPACE="database"
STS_NAME="mongo"
DB_NAME="ecommerce_db"
MONGO_USER="admin"
MONGO_PASS="SecurePass123"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
BACKUP_NAME="backup_${DB_NAME}_${TIMESTAMP}.dump.gz"
REMOTE_PATH="/dev/shm/${BACKUP_NAME}"
LOCAL_DEST="./backups/${BACKUP_NAME}"

echo "=== [1/4] Nhận diện Node Secondary để sao lưu ==="
TARGET_POD=$(kubectl exec -n $NAMESPACE ${STS_NAME}-0 -- mongosh --quiet \
  -u "$MONGO_USER" -p "$MONGO_PASS" --authenticationDatabase "admin" \
  --eval "
    const status = rs.status();
    if (!status.ok) {
      print('${STS_NAME}-0');
    } else {
      const sec = status.members.find(m => m.stateStr === 'SECONDARY');
      print(sec ? sec.name.split(':')[0].split('.')[0] : '${STS_NAME}-0');
    }
  ")
echo "--> Pod được chọn: $TARGET_POD"

# Bẫy lỗi: Đảm bảo dù lệnh có bị ngắt (Ctrl+C hoặc lỗi), file trong /dev/shm VẪN BỊ XÓA
cleanup() {
  echo "=== [CLEANUP] Thu hồi RAM: Xóa file tạm trong /dev/shm trên $TARGET_POD ==="
  kubectl exec -n $NAMESPACE "$TARGET_POD" -- rm -f "$REMOTE_PATH" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

echo "=== [2/4] Thực hiện mongodump vào /dev/shm ==="
kubectl exec -n $NAMESPACE "$TARGET_POD" -- \
  mongodump \
    --username="$MONGO_USER" \
    --password="$MONGO_PASS" \
    --authenticationDatabase="admin" \
    --db="$DB_NAME" \
    --archive="$REMOTE_PATH" \
    --gzip

echo "=== [3/4] Copy file về máy chủ lưu trữ ==="
mkdir -p "$(dirname "$LOCAL_DEST")"
kubectl cp "$NAMESPACE/$TARGET_POD:$REMOTE_PATH" "$LOCAL_DEST"
echo "--> Đã lưu thành công tại: $LOCAL_DEST"

echo "=== [4/4] Hoàn tất sao lưu an toàn! ==="
```

---

## Tóm tắt & Bước đệm sang Bài 5

* Luôn ưu tiên `mongodump/mongorestore` cho công tác Disaster Recovery.
* Thư mục `/dev/shm` là cứu cánh khi Pod bị cấm ghi rootfs, nhưng phải dọn dẹp ngay sau khi dùng bằng lệnh `trap`.
* Khi vận hành Replica Set: **Backup từ Secondary, Restore vào Primary**.
* Sử dụng kỹ thuật **UNIX Pipe Streaming** để xử lý các database dung lượng lớn mà không sợ tràn RAM `/dev/shm`.

👉 **Ở bài viết cuối cùng (Bài 5):** Chúng ta sẽ khép lại toàn bộ series với chủ đề nâng cao: **"Giám Sát, Bảo Mật & Disaster Recovery Nâng Cao"** — Triển khai Prometheus mongodb-exporter, mã hóa TLS/mTLS, NetworkPolicy và kỹ thuật chụp CSI Volume Snapshot trong tích tắc.
