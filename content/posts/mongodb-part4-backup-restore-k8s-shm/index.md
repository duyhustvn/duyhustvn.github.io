---
title: "MongoDB Thực Chiến (Phần 4): Cẩm Nang Backup & Restore – Từ Môi Trường Thường Đến Kubernetes (Single Node, Replica Set & Giải Pháp /dev/shm)"
date: 2026-11-07T17:00:00+07:00
draft: false
description: "Cẩm nang toàn diện về sao lưu và phục hồi dữ liệu trong MongoDB: Phân biệt mongodump vs mongoexport. Nhập xuất dữ liệu toàn bộ DB và theo từng Bảng (Collection) cụ thể dạng BSON/CSV/JSON. Hướng dẫn chi tiết kịch bản Kubernetes qua thư mục chia sẻ /dev/shm cho cả Single Node và Replica Set."
summary: "Hướng dẫn thực chiến Backup & Restore MongoDB: Sao lưu toàn bộ hoặc theo từng Bảng (Collection) dạng BSON và CSV/JSON. Xử lý triệt để trên Kubernetes qua /dev/shm cho Single Node và Replica Set, cùng kỹ thuật streaming không tốn RAM."
tags: ["MongoDB", "Backup", "Restore", "Kubernetes", "K8s", "DevOps", "Database", "mongodump", "mongoexport"]
categories: ["Kubernetes", "Database", "DevOps"]
series: ["MongoDB Thực Chiến"]
series_order: 4
showTableOfContents: true
---

Sao lưu (Backup) và Phục hồi (Restore) dữ liệu là "tấm lưới bảo hiểm" sống còn của bất kỳ hệ thống dữ liệu nào. Nếu việc backup trên máy chủ truyền thống (Bare-metal hoặc Virtual Machine) tương đối thẳng thắn, thì khi bước vào thế giới **Kubernetes**, bạn sẽ gặp phải những rào cản chính sách bảo mật khắt khe: Container non-root, hệ thống tệp gốc ở chế độ chỉ đọc (`readOnlyRootFilesystem: true`), và **dữ liệu chỉ được phép sao chép tạm thời vào phân vùng bộ nhớ chia sẻ `/dev/shm`**.

Bên cạnh việc sao lưu toàn bộ cơ sở dữ liệu, một nhu cầu cực kỳ phổ biến trong vận hành thực tế là **nhập / xuất dữ liệu theo từng Bảng (Collection) cụ thể** — ví dụ: trích xuất riêng bảng `orders` để phân tích báo cáo, hoặc chỉ restore lại bảng `users` khi xảy ra sự cố xóa nhầm.

Bài viết này sẽ hướng dẫn bạn chi tiết từ cấp độ toàn bộ Database đến từng Bảng riêng biệt (cả định dạng BSON chuẩn lẫn CSV/JSON), áp dụng cho môi trường thường và 2 chế độ chạy trên Kubernetes (**Single Node** & **Replica Set**).

---

## 1. Phân biệt công cụ: `mongodump/mongorestore` vs `mongoexport/mongoimport`

Trước khi bắt tay vào thực hành, cần phân biệt rõ 2 bộ công cụ có sẵn trong gói MongoDB Database Tools:

| Tiêu chí so sánh | `mongodump` / `mongorestore` | `mongoexport` / `mongoimport` |
| :--- | :--- | :--- |
| **Định dạng dữ liệu** | **Binary (BSON)** + Metadata JSON | **Plain Text (JSON hoặc CSV)** |
| **Bảo toàn kiểu dữ liệu** | **100% chính xác** (ObjectId, Date, NumberLong, Binary,...) | Dễ mất mát định dạng chặt chẽ của BSON |
| **Chỉ số (Indexes)** | **Tự động lưu và khôi phục toàn bộ Index** | **Không** lưu Index (phải tạo lại bằng tay) |
| **Tốc độ xử lý** | Nhanh hơn gấp nhiều lần (xử lý nhị phân) | Chậm hơn (tốn CPU parse text/json) |
| **Mục đích sử dụng** | **Backup & Restore hệ thống (Disaster Recovery)** | Export báo cáo ra Excel, ETL sang Data Warehouse |

> 💡 **Quy tắc vàng:** Cho mục đích sao lưu thảm họa và phục hồi dữ liệu hệ thống, **luôn luôn dùng `mongodump` và `mongorestore`**. Chỉ dùng `mongoexport`/`mongoimport` khi cần file dạng bảng tính (CSV/JSON) để đọc hoặc trao đổi với hệ thống khác.

---

## 2. Trường hợp 1: Chạy bình thường (Bare Metal / VM / Docker Host)

### 2.1. Sao lưu & Phục hồi toàn bộ Database (`mongodump` / `mongorestore`)

Thay vì dump ra hàng ngàn thư mục và file `.bson` rời rạc, hãy sử dụng 2 cờ `--archive` và `--gzip` để gom toàn bộ thành **một file nén duy nhất**:

```bash
# 1. Backup toàn bộ database ecommerce_db ra 1 file duy nhất
mongodump \
  --uri="mongodb://admin:SecurePass123@127.0.0.1:27017/?authSource=admin" \
  --db=ecommerce_db \
  --archive=/var/backups/mongodb/ecommerce_all_$(date +%F).dump.gz \
  --gzip

# 2. Restore toàn bộ database (cờ --drop để xóa bảng cũ trước khi nạp lại)
mongorestore \
  --uri="mongodb://admin:SecurePass123@127.0.0.1:27017/?authSource=admin" \
  --nsInclude="ecommerce_db.*" \
  --archive=/var/backups/mongodb/ecommerce_all_2026-11-07.dump.gz \
  --gzip \
  --drop
```

---

### 2.2. Sao lưu & Phục hồi theo từng BẢNG (Collection) cụ thể dạng BSON

Khi bạn chỉ muốn sao lưu hoặc khôi phục một bảng duy nhất (giữ nguyên kiểu dữ liệu BSON và toàn bộ Index):

#### A. Backup riêng 1 Bảng:
Dùng cờ `--collection` (hoặc `-c`):
```bash
# Backup riêng bảng "orders"
mongodump \
  --uri="mongodb://admin:SecurePass123@127.0.0.1:27017/?authSource=admin" \
  --db=ecommerce_db \
  --collection=orders \
  --archive=/var/backups/mongodb/orders_table_$(date +%F).dump.gz \
  --gzip
```

💡 **Mẹo nâng cao (Lọc dữ liệu bảng khi dump):** Dùng cờ `--query` để chỉ backup các bản ghi thỏa mãn điều kiện (ví dụ chỉ lấy đơn hàng năm 2026):
```bash
mongodump \
  --uri="mongodb://admin:SecurePass123@127.0.0.1:27017/?authSource=admin" \
  --db=ecommerce_db \
  --collection=orders \
  --query='{"created_at": {"$gte": {"$date": "2026-01-01T00:00:00Z"}}}' \
  --archive=/var/backups/mongodb/orders_2026.dump.gz \
  --gzip
```

#### B. Restore riêng 1 Bảng:
Dùng cờ `--nsInclude` để chỉ định chính xác namespace `database.collection`:
```bash
mongorestore \
  --uri="mongodb://admin:SecurePass123@127.0.0.1:27017/?authSource=admin" \
  --nsInclude="ecommerce_db.orders" \
  --archive=/var/backups/mongodb/orders_table_2026-11-07.dump.gz \
  --gzip \
  --drop
```

💡 **Mẹo đổi tên Bảng khi Restore (Table Remapping):** Restore bảng `orders` từ file backup thành một bảng mới tên là `orders_recovered` để kiểm tra trước khi ghi đè bảng chính:
```bash
mongorestore \
  --uri="mongodb://admin:SecurePass123@127.0.0.1:27017/?authSource=admin" \
  --nsFrom="ecommerce_db.orders" \
  --nsTo="ecommerce_db.orders_recovered" \
  --archive=/var/backups/mongodb/orders_table_2026-11-07.dump.gz \
  --gzip
```

---

### 2.3. Xuất & Nhập dữ liệu theo BẢNG dạng CSV / JSON (`mongoexport` / `mongoimport`)

Khi bạn cần xuất bảng dữ liệu ra file CSV để mở bằng Excel, Google Sheets hoặc nạp dữ liệu từ file ngoài vào bảng:

#### A. Xuất dữ liệu Bảng ra CSV / JSON:
```bash
# Xuất bảng "users" ra định dạng CSV với các cột chỉ định
mongoexport \
  --uri="mongodb://admin:SecurePass123@127.0.0.1:27017/?authSource=admin" \
  --db=ecommerce_db \
  --collection=users \
  --type=csv \
  --fields="_id,username,email,status,created_at" \
  --out=/var/backups/mongodb/users_export.csv

# Hoặc xuất ra file JSON (1 document trên 1 dòng)
mongoexport \
  --uri="mongodb://admin:SecurePass123@127.0.0.1:27017/?authSource=admin" \
  --db=ecommerce_db \
  --collection=users \
  --type=json \
  --out=/var/backups/mongodb/users_export.json
```

#### B. Nhập dữ liệu từ CSV / JSON vào Bảng:
```bash
# Nhập file CSV vào bảng "users"
# --headerline: Dòng đầu tiên của CSV là tiêu đề cột
# --mode=upsert: Nếu trùng khóa (dựa vào --upsertFields) thì cập nhật, chưa có thì chèn mới
mongoimport \
  --uri="mongodb://admin:SecurePass123@127.0.0.1:27017/?authSource=admin" \
  --db=ecommerce_db \
  --collection=users \
  --type=csv \
  --headerline \
  --file=/var/backups/mongodb/users_export.csv \
  --mode=upsert \
  --upsertFields=email
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
        BackupFile["📁 file_backup (.dump.gz / .csv)"]
    end

    subgraph Pod["☸️ MongoDB Pod trong Kubernetes"]
        subgraph Mem["💾 RAM (tmpfs)"]
            SHM["📂 /dev/shm/<br/>(Bộ nhớ chia sẻ)"]
        end
        Engine["⚙️ MongoDB Process (mongod)"]
    end

    BackupFile <-->|kubectl cp| SHM
    SHM <-->|mongodump / mongoexport<br/>mongorestore / mongoimport| Engine
{{< /mermaid >}}

### ⚠️ Lưu ý sống còn về `/dev/shm`:
1. **Dung lượng mặc định:** Thường là **64MB** trên Linux container trừ khi Pod spec có mount `emptyDir: { medium: Memory }`.
2. **Nguy cơ OOMKilled:** Dữ liệu trong `/dev/shm` nằm trực tiếp trên RAM. Nếu bạn dump file lớn vào đây mà quên xóa, Pod sẽ bị tràn RAM và Kubernetes sẽ tiêu diệt Pod ngay lập tức (`OOMKilled`)! **Bắt buộc phải xóa file ngay sau khi xử lý xong.**

---

## 4. Kubernetes Chế Độ 1: Chạy Đơn Node (Single Node)

Mô hình này thường áp dụng cho môi trường Staging/Dev hoặc các dịch vụ phụ trợ chạy 1 Pod độc lập (ví dụ Pod: `mongodb-single-0`).

### 4.1. Backup & Restore toàn bộ Database qua `/dev/shm`:
```bash
NAMESPACE="database"
POD_NAME="mongodb-single-0"
BACKUP_FILE="backup_db_$(date +%Y%m%d_%H%M%S).dump.gz"

# 1. Dump vào /dev/shm của Pod
kubectl exec -n $NAMESPACE $POD_NAME -- \
  mongodump \
    -u "admin" -p "SecurePass123" --authenticationDatabase "admin" \
    --db="ecommerce_db" \
    --archive=/dev/shm/$BACKUP_FILE \
    --gzip

# 2. Kéo file về máy quản trị
kubectl cp $NAMESPACE/$POD_NAME:/dev/shm/$BACKUP_FILE ./$BACKUP_FILE

# 3. DỌN DẸP NGAY LẬP TỨC để giải phóng RAM của Pod
kubectl exec -n $NAMESPACE $POD_NAME -- rm -f /dev/shm/$BACKUP_FILE
```

### 4.2. Xuất & Nhập theo từng BẢNG cụ thể qua `/dev/shm`:

#### Xuất riêng 1 bảng ra CSV từ Pod qua `/dev/shm`:
```bash
CSV_FILE="orders_$(date +%F).csv"

# 1. Chạy mongoexport ghi file vào /dev/shm của Pod
kubectl exec -n $NAMESPACE $POD_NAME -- \
  mongoexport \
    -u "admin" -p "SecurePass123" --authenticationDatabase "admin" \
    --db="ecommerce_db" \
    --collection="orders" \
    --type=csv \
    --fields="_id,order_code,customer_id,total_amount,status,created_at" \
    --out=/dev/shm/$CSV_FILE

# 2. Kéo file CSV về máy quản trị
kubectl cp $NAMESPACE/$POD_NAME:/dev/shm/$CSV_FILE ./$CSV_FILE

# 3. Xóa file trong /dev/shm
kubectl exec -n $NAMESPACE $POD_NAME -- rm -f /dev/shm/$CSV_FILE
```

#### Nhập file CSV từ máy quản trị vào một bảng trong Pod qua `/dev/shm`:
```bash
LOCAL_CSV="./import_customers.csv"
REMOTE_FILE="/dev/shm/customers_temp.csv"

# 1. Copy file CSV vào /dev/shm của Pod
kubectl cp $LOCAL_CSV $NAMESPACE/$POD_NAME:$REMOTE_FILE

# 2. Thực thi mongoimport chèn/cập nhật vào bảng "customers"
kubectl exec -n $NAMESPACE $POD_NAME -- \
  mongoimport \
    -u "admin" -p "SecurePass123" --authenticationDatabase "admin" \
    --db="ecommerce_db" \
    --collection="customers" \
    --type=csv \
    --headerline \
    --file=$REMOTE_FILE \
    --mode=upsert \
    --upsertFields=email

# 3. Dọn dẹp RAM ngay sau khi import xong
kubectl exec -n $NAMESPACE $POD_NAME -- rm -f $REMOTE_FILE
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

    BackupAction["📥 XUẤT / BACKUP TÁC VỤ"] -.->|Nên thực hiện trên| Secondary
    RestoreAction["📤 NHẬP / RESTORE TÁC VỤ"] ==>|BẮT BUỘC ghi vào| Primary
    Primary -->|Tự động sync qua Oplog| Secondary
{{< /mermaid >}}

* 📥 **Nguyên tắc Xuất / Backup:** Nên thực hiện trên node **SECONDARY** để tránh chiếm dụng CPU và I/O của node Primary đang phục vụ khách hàng. Thêm cờ `--oplog` nếu backup toàn bộ cluster.
* 📤 **Nguyên tắc Nhập / Restore:** **BẮT BUỘC PHẢI THỰC HIỆN TRÊN NODE PRIMARY!** Các node Secondary ở trạng thái chỉ đọc (Read-Only). Mọi dữ liệu nạp vào Primary sẽ tự động được sao chép sang toàn bộ Secondary thông qua Oplog.

---

### 5.1. Backup & Restore Replica Set qua `/dev/shm`

#### A. Backup từ Secondary Pod (`mongo-1`):
```bash
NAMESPACE="database"
TARGET_POD="mongo-1" # Node Secondary
BACKUP_FILE="rs_backup_$(date +%Y%m%d_%H%M%S).dump.gz"

# 1. Dump vào /dev/shm của Secondary Pod kèm cờ --oplog
kubectl exec -n $NAMESPACE $TARGET_POD -- \
  mongodump \
    -u "admin" -p "SecurePass123" --authenticationDatabase "admin" \
    --archive=/dev/shm/$BACKUP_FILE \
    --gzip \
    --oplog

# 2. Copy file về máy local
kubectl cp $NAMESPACE/$TARGET_POD:/dev/shm/$BACKUP_FILE ./$BACKUP_FILE

# 3. Dọn dẹp /dev/shm trên Pod
kubectl exec -n $NAMESPACE $TARGET_POD -- rm -f /dev/shm/$BACKUP_FILE
```

#### B. Restore vào Primary Pod:
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
    -u "admin" -p "SecurePass123" --authenticationDatabase "admin" \
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

### 5.2. Nhập & Xuất theo từng BẢNG cụ thể trên Replica Set qua `/dev/shm`

#### Xuất riêng 1 Bảng từ Secondary Node (`mongo-1`):
```bash
BACKUP_POD="mongo-1"
TABLE_FILE="orders_table.dump.gz"

# Dump riêng bảng "orders" vào /dev/shm của Secondary
kubectl exec -n $NAMESPACE $BACKUP_POD -- \
  mongodump \
    -u "admin" -p "SecurePass123" --authenticationDatabase "admin" \
    --db="ecommerce_db" \
    --collection="orders" \
    --archive=/dev/shm/$TABLE_FILE \
    --gzip

# Copy về máy và dọn dẹp
kubectl cp $NAMESPACE/$BACKUP_POD:/dev/shm/$TABLE_FILE ./$TABLE_FILE
kubectl exec -n $NAMESPACE $BACKUP_POD -- rm -f /dev/shm/$TABLE_FILE
```

#### Nhập lại riêng Bảng đó vào Primary Node:
```bash
# 1. Tự động lấy Primary Pod
PRIMARY_POD=$(kubectl exec -n $NAMESPACE mongo-0 -- mongosh --quiet \
  -u "admin" -p "SecurePass123" --authenticationDatabase "admin" \
  --eval "db.isMaster().primary.split(':')[0].split('.')[0]")

# 2. Copy vào /dev/shm của PRIMARY
kubectl cp ./$TABLE_FILE $NAMESPACE/$PRIMARY_POD:/dev/shm/$TABLE_FILE

# 3. Restore riêng bảng orders với cờ --drop
kubectl exec -n $NAMESPACE $PRIMARY_POD -- \
  mongorestore \
    -u "admin" -p "SecurePass123" --authenticationDatabase "admin" \
    --nsInclude="ecommerce_db.orders" \
    --archive=/dev/shm/$TABLE_FILE \
    --gzip \
    --drop

# 4. Xóa file trên Primary
kubectl exec -n $NAMESPACE $PRIMARY_POD -- rm -f /dev/shm/$TABLE_FILE
```

---

## 6. Pro-Tip: Kỹ Thuật Streaming Pipe (Bypass hoàn toàn giới hạn `/dev/shm`)

Trong thực tế, file backup của bảng hoặc database có thể nặng **50GB hoặc 100GB**, trong khi `/dev/shm` chỉ có vài trăm MB RAM. Nếu lưu file tạm vào `/dev/shm`, Pod chắc chắn sẽ chết vì OOM.

Bạn hoàn toàn có thể **bỏ qua việc lưu file vào `/dev/shm`** bằng cách dùng ống dẫn dữ liệu (UNIX Pipe) trực tiếp qua `kubectl exec`:

### Backup toàn bộ DB dạng Stream (Không tốn 1 byte đĩa hay RAM trong Pod):
```bash
kubectl exec -i -n database mongo-1 -- \
  mongodump \
    -u "admin" -p "SecurePass123" --authenticationDatabase "admin" \
    --archive \
    --gzip > ./backup_direct_stream.dump.gz
```
*(Dữ liệu từ stdout của container được bắn thẳng qua mạng và ghi trực tiếp vào ổ cứng máy host).*

### Xuất 1 BẢNG ra file CSV dạng Stream trực tiếp:
```bash
kubectl exec -i -n database mongo-1 -- \
  mongoexport \
    -u "admin" -p "SecurePass123" --authenticationDatabase "admin" \
    --db="ecommerce_db" \
    --collection="orders" \
    --type=csv \
    --fields="_id,order_code,total_amount,status" > ./orders_stream.csv
```

### Restore 1 BẢNG dạng Stream (Đẩy trực tiếp từ máy host vào Pod Primary):
```bash
cat ./orders_table.dump.gz | kubectl exec -i -n database $PRIMARY_POD -- \
  mongorestore \
    -u "admin" -p "SecurePass123" --authenticationDatabase "admin" \
    --nsInclude="ecommerce_db.orders" \
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
COLLECTION_NAME="orders" # Để trống nếu muốn backup cả DB
MONGO_USER="admin"
MONGO_PASS="SecurePass123"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)

if [ -n "$COLLECTION_NAME" ]; then
  BACKUP_NAME="backup_${DB_NAME}_${COLLECTION_NAME}_${TIMESTAMP}.dump.gz"
  EXTRA_ARGS="--collection=${COLLECTION_NAME}"
else
  BACKUP_NAME="backup_${DB_NAME}_all_${TIMESTAMP}.dump.gz"
  EXTRA_ARGS=""
fi

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
    -u "$MONGO_USER" -p "$MONGO_PASS" --authenticationDatabase "admin" \
    --db="$DB_NAME" \
    $EXTRA_ARGS \
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

* Luôn ưu tiên `mongodump/mongorestore` cho công tác Disaster Recovery (giữ nguyên BSON & Index). Dùng `mongoexport/mongoimport` khi cần xử lý dữ liệu bảng tính CSV/JSON.
* Thao tác theo từng bảng: Dùng cờ `--collection` khi dump, `--nsInclude` khi restore, `--query` để lọc dữ liệu, và `--nsFrom` / `--nsTo` khi cần đổi tên bảng.
* Thư mục `/dev/shm` là cứu cánh khi Pod bị cấm ghi rootfs, nhưng phải dọn dẹp ngay sau khi dùng bằng lệnh `trap`.
* Khi vận hành Replica Set: **Xuất/Dump từ Secondary, Nhập/Restore vào Primary**.
* Sử dụng kỹ thuật **UNIX Pipe Streaming** để xử lý các bảng hoặc database dung lượng lớn mà không sợ tràn RAM `/dev/shm`.

👉 **Ở bài viết cuối cùng (Bài 5):** Chúng ta sẽ khép lại toàn bộ series với chủ đề nâng cao: **"Giám Sát, Bảo Mật & Disaster Recovery Nâng Cao"** — Triển khai Prometheus mongodb-exporter, mã hóa TLS/mTLS, NetworkPolicy và kỹ thuật chụp CSI Volume Snapshot trong tích tắc.
