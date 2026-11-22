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

Sao lưu (Backup) và Phục hồi (Restore) dữ liệu là "tấm lưới bảo hiểm" sống còn của bất kỳ hệ thống cơ sở dữ liệu nào. Nếu việc backup trên máy chủ truyền thống (Bare-metal hoặc Virtual Machine) tương đối thẳng thắn, thì khi bước vào thế giới **Kubernetes**, bạn sẽ gặp phải những rào cản chính sách bảo mật khắt khe: Container non-root, hệ thống tệp gốc ở chế độ chỉ đọc (`readOnlyRootFilesystem: true`), và **dữ liệu chỉ được phép sao chép tạm thời vào phân vùng bộ nhớ chia sẻ `/dev/shm`**.

Bên cạnh việc sao lưu toàn bộ cơ sở dữ liệu, một nhu cầu cực kỳ phổ biến trong vận hành thực tế là **nhập / xuất dữ liệu theo từng Bảng (Collection) cụ thể** — ví dụ: trích xuất riêng bảng `orders` để phân tích báo cáo, hoặc chỉ restore lại bảng `users` khi xảy ra sự cố xóa nhầm mà không làm ảnh hưởng đến dữ liệu hiện có.

Bài viết này sẽ hướng dẫn bạn chi tiết từ cấp độ toàn bộ Database đến từng Bảng riêng biệt (cả định dạng BSON chuẩn lẫn CSV/JSON), áp dụng từ môi trường truyền thống đến 2 mô hình chạy trên Kubernetes (**Single Node** & **Replica Set**).

---

## 1. Phân Biệt Công Cụ: `mongodump/mongorestore` vs `mongoexport/mongoimport`

Trước khi bắt tay vào thực hành, cần phân biệt rõ 2 bộ công cụ có sẵn trong gói **MongoDB Database Tools**:

| Tiêu chí so sánh | `mongodump` / `mongorestore` | `mongoexport` / `mongoimport` |
| :--- | :--- | :--- |
| **Định dạng dữ liệu** | **Binary (BSON)** + Metadata JSON | **Plain Text (JSON hoặc CSV)** |
| **Bảo toàn kiểu dữ liệu** | **100% chính xác** (`ObjectId`, `Date`, `Decimal128`, `Binary`,...) | Dễ mất mát kiểu dữ liệu đặc thù của BSON (đặc biệt khi xuất ra CSV) |
| **Chỉ số (Indexes)** | **Tự động lưu và khôi phục toàn bộ Index** | **Không** lưu Index (phải tạo lại bằng tay) |
| **Tốc độ xử lý** | Nhanh hơn gấp nhiều lần (xử lý nhị phân trực tiếp) | Chậm hơn (tốn CPU parse text/JSON) |
| **Mục đích sử dụng** | **Backup & Restore hệ thống (Disaster Recovery)** | Xuất báo cáo ra Excel, ETL sang Data Warehouse |

> 💡 **Quy tắc vàng:** Cho mục đích sao lưu thảm họa và phục hồi dữ liệu hệ thống, **luôn luôn dùng `mongodump` và `mongorestore`**. Chỉ dùng `mongoexport` / `mongoimport` khi cần file dạng bảng tính (CSV/JSON) để đọc hoặc trao đổi với hệ thống khác.

---

## 2. Môi Trường Truyền Thống (Bare Metal / VM / Docker Host)

### 2.1. Sao lưu & Phục hồi toàn bộ Database (`mongodump` / `mongorestore`)

Thay vì dump ra hàng ngàn thư mục và file `.bson` rời rạc, hãy sử dụng kết hợp 2 cờ `--archive` và `--gzip` để gom toàn bộ thành **một file nén duy nhất**:

```bash
# 1. Backup toàn bộ database ecommerce_db ra 1 file nén duy nhất
mongodump \
  --uri="mongodb://admin:SecurePass123@127.0.0.1:27017/?authSource=admin" \
  --db=ecommerce_db \
  --archive=/var/backups/mongodb/ecommerce_all_$(date +%F).dump.gz \
  --gzip

# 2. Restore toàn bộ database (cờ --drop để xóa collection cũ trước khi nạp lại)
mongorestore \
  --uri="mongodb://admin:SecurePass123@127.0.0.1:27017/?authSource=admin" \
  --nsInclude="ecommerce_db.*" \
  --archive=/var/backups/mongodb/ecommerce_all_2026-11-07.dump.gz \
  --gzip \
  --drop
```

---

### 2.2. Sao lưu & Phục hồi theo từng Bảng (Collection) dạng BSON

Khi bạn chỉ muốn sao lưu hoặc khôi phục một bảng duy nhất nhưng vẫn giữ nguyên kiểu dữ liệu BSON và toàn bộ Index:

#### A. Backup riêng 1 Bảng (Có hoặc không có điều kiện lọc)
Dùng cờ `--collection` (hoặc `-c`) để chỉ định bảng cần sao lưu:

```bash
# Backup toàn bộ bảng "orders"
mongodump \
  --uri="mongodb://admin:SecurePass123@127.0.0.1:27017/?authSource=admin" \
  --db=ecommerce_db \
  --collection=orders \
  --archive=/var/backups/mongodb/orders_table_$(date +%F).dump.gz \
  --gzip
```

Khi chỉ muốn backup một tập con bản ghi trong bảng (ví dụ: chỉ lấy đơn hàng năm 2026 hoặc đơn hàng đã hoàn tất), bạn có thể lọc bằng `--query` hoặc `--queryFile`:

- **Cách 1: Dùng `--query` trực tiếp trên dòng lệnh (Cho câu lọc đơn giản)**
  ```bash
  mongodump \
    --uri="mongodb://admin:SecurePass123@127.0.0.1:27017/?authSource=admin" \
    --db=ecommerce_db \
    --collection=orders \
    --query='{"status": "COMPLETED"}' \
    --archive=/var/backups/mongodb/orders_completed.dump.gz \
    --gzip
  ```

- **Cách 2: Dùng `--queryFile` (Khuyên dùng cho Production / Câu lọc phức tạp)**
  > ⚠️ **Tại sao nên dùng `--queryFile` thay vì `--query`?**  
  > Khi câu truy vấn chứa các toán tử như `$and`, `$or`, `$gte` hoặc cú pháp Extended JSON (`{"$date": "..."}`, `{"$oid": "..."}`), việc truyền trực tiếp chuỗi JSON trên terminal rất dễ lỗi do Shell (Bash/Zsh) hiểu nhầm dấu `$` thành biến môi trường hoặc xung đột dấu nháy đơn/kép. Tách truy vấn ra file JSON giúp tránh hoàn toàn lỗi escape ký tự và dễ kiểm soát.

  *Bước 1:* Tạo file điều kiện lọc `/var/backups/mongodb/filter_orders_2026.json`:
  ```json
  {
    "status": "COMPLETED",
    "total_amount": { "$gte": 500000 },
    "created_at": {
      "$gte": { "$date": "2026-01-01T00:00:00Z" },
      "$lt": { "$date": "2027-01-01T00:00:00Z" }
    }
  }
  ```

  *Bước 2:* Chạy `mongodump` với `--queryFile`:
  ```bash
  mongodump \
    --uri="mongodb://admin:SecurePass123@127.0.0.1:27017/?authSource=admin" \
    --db=ecommerce_db \
    --collection=orders \
    --queryFile=/var/backups/mongodb/filter_orders_2026.json \
    --archive=/var/backups/mongodb/orders_2026.dump.gz \
    --gzip
  ```

#### B. Restore riêng 1 Bảng (Và kỹ thuật đổi tên Bảng khi Restore)
Dùng cờ `--nsInclude` để chỉ định chính xác namespace `database.collection`:

```bash
mongorestore \
  --uri="mongodb://admin:SecurePass123@127.0.0.1:27017/?authSource=admin" \
  --nsInclude="ecommerce_db.orders" \
  --archive=/var/backups/mongodb/orders_table_2026-11-07.dump.gz \
  --gzip \
  --drop
```

> 💡 **Mẹo đổi tên Bảng khi Restore (Collection Remapping):** Bạn có thể restore bảng `orders` từ file backup thành một bảng mới tên là `orders_recovered` thông qua cặp cờ `--nsFrom` và `--nsTo` để kiểm tra dữ liệu an toàn trước khi quyết định ghi đè bảng chính:
> ```bash
> mongorestore \
>   --uri="mongodb://admin:SecurePass123@127.0.0.1:27017/?authSource=admin" \
>   --nsFrom="ecommerce_db.orders" \
>   --nsTo="ecommerce_db.orders_recovered" \
>   --archive=/var/backups/mongodb/orders_table_2026-11-07.dump.gz \
>   --gzip
> ```

---

### 2.3. Xuất & Nhập dữ liệu theo Bảng dạng CSV / JSON (`mongoexport` / `mongoimport`)

Khi bạn cần xuất bảng dữ liệu ra file CSV để mở bằng Excel, Google Sheets hoặc đồng bộ với hệ thống báo cáo bên ngoài:

#### A. Xuất dữ liệu Bảng ra CSV / JSON
```bash
# 1. Xuất bảng "users" ra định dạng CSV với các cột chỉ định
mongoexport \
  --uri="mongodb://admin:SecurePass123@127.0.0.1:27017/?authSource=admin" \
  --db=ecommerce_db \
  --collection=users \
  --type=csv \
  --fields="_id,username,email,status,created_at" \
  --out=/var/backups/mongodb/users_export.csv

# 2. Xuất bảng "users" ra định dạng JSON (1 document trên 1 dòng - JSON Lines)
mongoexport \
  --uri="mongodb://admin:SecurePass123@127.0.0.1:27017/?authSource=admin" \
  --db=ecommerce_db \
  --collection=users \
  --type=json \
  --out=/var/backups/mongodb/users_export.json

# 3. Xuất dữ liệu bảng ra CSV có lọc theo file query JSON (--queryFile)
mongoexport \
  --uri="mongodb://admin:SecurePass123@127.0.0.1:27017/?authSource=admin" \
  --db=ecommerce_db \
  --collection=orders \
  --queryFile=/var/backups/mongodb/filter_orders_2026.json \
  --type=csv \
  --fields="_id,order_code,customer_id,total_amount,status,created_at" \
  --out=/var/backups/mongodb/orders_vip_2026.csv
```

#### B. Nhập dữ liệu từ CSV / JSON vào Bảng
```bash
# Nhập file CSV vào bảng "users"
# --headerline: Lấy dòng đầu tiên của CSV làm tên trường
# --mode=upsert: Nếu trùng khóa (--upsertFields) thì cập nhật, chưa có thì chèn mới
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

### 2.4. Chiến lược Nhập dữ liệu lọc theo Query mà KHÔNG xóa bảng đích (Skip, Replace, Merge)

Trong vận hành thực tế, có một kịch bản rất dễ gây tai nạn:
- Bạn trích xuất một phần dữ liệu theo query (ví dụ: chỉ xuất đơn hàng tháng 10/2026).
- Bạn muốn nạp tập dữ liệu này vào một Database khác (hoặc nạp đè lại vào chính Database hiện tại để vá lỗi dữ liệu).

> 🚨 **CẢNH BÁO TỬ HUYỆT:** Khi nạp một bản backup từng phần (partial dump), **TUYỆT ĐỐI KHÔNG DÙNG cờ `--drop`**! Nếu có `--drop`, `mongorestore` sẽ **xóa sạch toàn bộ collection đích** (bay mất cả dữ liệu các tháng 1, 2, 3...) trước khi nạp tập dữ liệu tháng 10 vào!

Vậy khi bỏ cờ `--drop`, làm sao kiểm soát hành vi khi trùng khóa `_id`: **Bỏ qua (Skip)**, **Ghi đè (Replace)** hay **Hòa trộn (Merge)**?

#### A. Đối với `mongoimport` (File CSV / JSON) — Dùng cờ `--mode`
`mongoimport` hỗ trợ trực tiếp 3 chế độ qua cờ `--mode`:
1. **`--mode=insert` (Mặc định - Skip khi trùng):** Chỉ chèn bản ghi mới. Nếu trùng `_id` (hoặc `--upsertFields`), bỏ qua bản ghi đó và tiếp tục chạy.
2. **`--mode=upsert` (Replace khi trùng):** Nếu chưa có thì chèn mới; nếu đã tồn tại thì **thay thế toàn bộ document cũ** bằng document từ file import.
3. **`--mode=merge` (Merge khi trùng):** Nếu đã tồn tại, giữ nguyên các trường cũ trong DB và chỉ cập nhật/bổ sung các trường có trong file import (tương tự `$set`).

```bash
mongoimport \
  --uri="mongodb://admin:SecurePass123@127.0.0.1:27017/?authSource=admin" \
  --db=target_db \
  --collection=orders \
  --type=json \
  --file=/var/backups/mongodb/orders_oct_2026.json \
  --mode=upsert \
  --upsertFields=_id
```

#### B. Đối với `mongorestore` (File BSON) — Kỹ thuật Staging Collection + `$merge`
Công cụ `mongorestore` **không có cờ `--mode=upsert`**. Khi chạy không có `--drop`, nó chỉ thực hiện `insert` và **mặc định bỏ qua (Skip)** mọi bản ghi bị trùng `_id` (báo `E11000 duplicate key error` rồi đi tiếp), nghĩa là dữ liệu cũ không được cập nhật.

Để **Replace (Ghi đè)** hoặc **Merge** với file dump BSON chuẩn Enterprise, hãy dùng quy trình **Staging Collection + `$merge` Pipeline**:

{{< mermaid >}}
flowchart LR
    Dump["📁 File Dump (Query)"] -->|"1. mongorestore"| Stage["📦 Bảng Tạm (orders_staging)<br/>(Dùng --drop an toàn)"]
    Stage -->|"2. Aggregation $merge"| Target["🎯 Bảng Đích (target_db.orders)<br/>whenMatched: 'replace'"]
    Target -->|"3. Dọn dẹp"| DropStage["🗑️ db.orders_staging.drop()"]
{{< /mermaid >}}

**Bước 1:** Restore file dump vào một bảng tạm (`orders_staging`) bằng `--nsFrom` / `--nsTo`:
```bash
mongorestore \
  --uri="mongodb://admin:SecurePass123@127.0.0.1:27017/?authSource=admin" \
  --nsFrom="ecommerce_db.orders" \
  --nsTo="target_db.orders_staging" \
  --archive=/var/backups/mongodb/orders_filtered_2026.dump.gz \
  --gzip \
  --drop
```

**Bước 2:** Chạy pipeline `$merge` trong `mongosh` để hòa trộn hoặc ghi đè từ bảng tạm sang bảng chính, sau đó xóa bảng tạm:
```javascript
use target_db

db.orders_staging.aggregate([
  {
    $merge: {
      into: "orders",           // Bảng đích cần cập nhật
      on: "_id",                // Khóa đối chiếu
      whenMatched: "replace",   // "replace" (Ghi đè) | "merge" (Hòa trộn) | "keepExisting" (Bỏ qua)
      whenNotMatched: "insert"  // Chèn mới nếu chưa tồn tại
    }
  }
])

// Bước 3: Xóa bảng tạm sau khi hoàn tất
db.orders_staging.drop()
```

---

## 3. Thách Thức Trên Kubernetes: Ràng Buộc Bảo Mật & `/dev/shm`

Trong các cụm Kubernetes tuân thủ tiêu chuẩn bảo mật doanh nghiệp (Hardened K8s):
- Container chạy dưới quyền non-root (`runAsNonRoot: true`).
- Hệ thống tệp gốc bị khóa chỉ đọc (`readOnlyRootFilesystem: true`).
- Ngoài phân vùng dữ liệu `/data/db`, thư mục khả ghi sẵn có duy nhất trên mọi Linux container mà không cần sửa đổi Pod manifest là **`/dev/shm`** (POSIX Shared Memory - `tmpfs`).

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

    BackupFile <-->|"kubectl cp"| SHM
    SHM <-->|"mongodump / mongoexport<br/>mongorestore / mongoimport"| Engine
{{< /mermaid >}}

### 3.1. Hai giới hạn sống còn của `/dev/shm` cần nắm rõ
1. **Giới hạn dung lượng mặc định:** Trên Kubernetes, `/dev/shm` mặc định có kích thước **64MB** (trừ khi Pod được mount một volume `emptyDir` với `medium: Memory`).
2. **Nguy cơ `OOMKilled`:** Mọi file lưu trong `/dev/shm` đều nằm trực tiếp trên **RAM** và được tính vào `resources.limits.memory` của Pod. Nếu bạn dump file lớn vào `/dev/shm` hoặc quên xóa file sau khi copy xong, Pod sẽ bị tràn RAM và bị Kernel tiêu diệt (`OOMKilled`). **Do đó, bắt buộc phải xóa ngay file tạm trong `/dev/shm` sau mỗi thao tác!**

---

## 4. Kịch Bản Kubernetes 1: MongoDB Single Node

Mô hình này thường dùng cho môi trường Dev/Staging hoặc các dịch vụ phụ trợ chạy 1 Pod độc lập (ví dụ Pod `mongodb-single-0` trong namespace `database`).

### 4.1. Backup & Restore toàn bộ Database qua `/dev/shm`

#### A. Chiều Backup (Từ Pod về Máy Quản Trị)
```bash
NAMESPACE="database"
POD_NAME="mongodb-single-0"
BACKUP_FILE="backup_db_$(date +%Y%m%d_%H%M%S).dump.gz"

# 1. Dump dữ liệu vào /dev/shm của Pod
kubectl exec -n $NAMESPACE $POD_NAME -- \
  mongodump \
    -u "admin" -p "SecurePass123" --authenticationDatabase "admin" \
    --db="ecommerce_db" \
    --archive=/dev/shm/$BACKUP_FILE \
    --gzip

# 2. Kéo file từ /dev/shm về máy quản trị
kubectl cp $NAMESPACE/$POD_NAME:/dev/shm/$BACKUP_FILE ./$BACKUP_FILE

# 3. DỌN DẸP NGAY LẬP TỨC để giải phóng RAM cho Pod
kubectl exec -n $NAMESPACE $POD_NAME -- rm -f /dev/shm/$BACKUP_FILE
```

#### B. Chiều Restore (Từ Máy Quản Trị vào Pod)
```bash
NAMESPACE="database"
POD_NAME="mongodb-single-0"
LOCAL_BACKUP="./backup_db_20261107_120000.dump.gz"
REMOTE_FILE="/dev/shm/restore_temp.dump.gz"

# 1. Đẩy file backup từ máy quản trị vào /dev/shm của Pod
kubectl cp $LOCAL_BACKUP $NAMESPACE/$POD_NAME:$REMOTE_FILE

# 2. Chạy mongorestore để khôi phục database
kubectl exec -n $NAMESPACE $POD_NAME -- \
  mongorestore \
    -u "admin" -p "SecurePass123" --authenticationDatabase "admin" \
    --nsInclude="ecommerce_db.*" \
    --archive=$REMOTE_FILE \
    --gzip \
    --drop

# 3. Xóa ngay file tạm trong /dev/shm
kubectl exec -n $NAMESPACE $POD_NAME -- rm -f $REMOTE_FILE
```

---

### 4.2. Xuất & Nhập theo từng Bảng cụ thể qua `/dev/shm`

#### A. Xuất riêng 1 Bảng ra CSV từ Pod
```bash
CSV_FILE="orders_$(date +%F).csv"

# 1. Chạy mongoexport ghi file vào /dev/shm
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

# 3. Xóa file tạm trong /dev/shm
kubectl exec -n $NAMESPACE $POD_NAME -- rm -f /dev/shm/$CSV_FILE
```

#### B. Nhập file CSV từ máy quản trị vào Bảng trong Pod
```bash
LOCAL_CSV="./import_customers.csv"
REMOTE_FILE="/dev/shm/customers_temp.csv"

# 1. Copy file CSV vào /dev/shm của Pod
kubectl cp $LOCAL_CSV $NAMESPACE/$POD_NAME:$REMOTE_FILE

# 2. Thực thi mongoimport với chế độ upsert
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

#### C. Lọc dữ liệu phức tạp với `--queryFile` qua `/dev/shm`
Để tránh lỗi escape ký tự `$` khi chạy qua hai lớp shell (`kubectl exec` + container shell), hãy tạo trực tiếp file `filter.json` trong `/dev/shm`:

```bash
# 1. Ghi trực tiếp nội dung JSON vào /dev/shm/filter.json trên Pod
kubectl exec -i -n $NAMESPACE $POD_NAME -- sh -c 'cat > /dev/shm/filter.json' << 'EOF'
{
  "status": "COMPLETED",
  "total_amount": { "$gte": 500000 },
  "created_at": { "$gte": { "$date": "2026-01-01T00:00:00Z" } }
}
EOF

# 2. Chạy mongodump với --queryFile
kubectl exec -n $NAMESPACE $POD_NAME -- \
  mongodump \
    -u "admin" -p "SecurePass123" --authenticationDatabase "admin" \
    --db="ecommerce_db" \
    --collection="orders" \
    --queryFile=/dev/shm/filter.json \
    --archive=/dev/shm/orders_vip.dump.gz \
    --gzip

# 3. Kéo file backup về máy quản trị và xóa sạch cả 2 file trong /dev/shm
kubectl cp $NAMESPACE/$POD_NAME:/dev/shm/orders_vip.dump.gz ./orders_vip.dump.gz
kubectl exec -n $NAMESPACE $POD_NAME -- rm -f /dev/shm/filter.json /dev/shm/orders_vip.dump.gz
```

---

## 5. Kịch Bản Kubernetes 2: Cụm MongoDB Replica Set (HA)

Khi vận hành cụm Replica Set gồm 3 Pod (`mongo-0`, `mongo-1`, `mongo-2`) như đã triển khai ở Phần 3, bạn phải tuân thủ quy tắc phân vai giữa **Primary** và **Secondary**:

{{< mermaid >}}
flowchart TD
    subgraph RS["☸️ MongoDB Replica Set"]
        Primary["⭐ PRIMARY NODE<br/>(mongo-0)<br/>Quyền: READ & WRITE"]
        Secondary["🔄 SECONDARY NODE<br/>(mongo-1 / mongo-2)<br/>Quyền: READ ONLY"]
    end

    BackupAction["📥 TÁC VỤ BACKUP / EXPORT"] -.->|"Nên thực hiện trên"| Secondary
    RestoreAction["📤 TÁC VỤ RESTORE / IMPORT"] ==>|"BẮT BUỘC ghi vào"| Primary
    Primary -->|"Tự động đồng bộ qua Oplog"| Secondary
{{< /mermaid >}}

- 📥 **Nguyên tắc Backup / Export:** Thực hiện trên **SECONDARY Pod** để không gây tải CPU và Disk I/O lên Primary Pod đang phục vụ ứng dụng Production.
- 📤 **Nguyên tắc Restore / Import:** **Bắt buộc thực hiện trên PRIMARY Pod**, vì các node Secondary ở chế độ chỉ đọc (`Read-Only`). Dữ liệu sau khi ghi vào Primary sẽ tự động nhân bản sang các Secondary qua Oplog.

---

### 5.1. Backup & Restore toàn cụm Replica Set (Có `--oplog`)

> ⚠️ **Lưu ý quan trọng về cờ `--oplog`:**  
> Cờ `--oplog` (khi `mongodump`) và `--oplogReplay` (khi `mongorestore`) giúp chụp lại cả các thao tác ghi phát sinh trong suốt thời gian lệnh backup đang chạy (Point-in-time consistency). Tuy nhiên, **`--oplog` CHỈ hoạt động khi bạn backup toàn bộ cluster (không truyền `--db` hoặc `--collection`)**. Nếu kết hợp `--oplog` với `--db`, MongoDB sẽ báo lỗi `bad option: --oplog mode only is supported on full dumps`.

#### A. Backup toàn cụm từ Secondary Pod (`mongo-1`)
```bash
NAMESPACE="database"
TARGET_POD="mongo-1" # Chọn Pod đang là Secondary
BACKUP_FILE="rs_full_backup_$(date +%Y%m%d_%H%M%S).dump.gz"

# 1. Dump toàn cụm vào /dev/shm của Secondary Pod kèm cờ --oplog
kubectl exec -n $NAMESPACE $TARGET_POD -- \
  mongodump \
    -u "admin" -p "SecurePass123" --authenticationDatabase "admin" \
    --archive=/dev/shm/$BACKUP_FILE \
    --gzip \
    --oplog

# 2. Copy file về máy quản trị
kubectl cp $NAMESPACE/$TARGET_POD:/dev/shm/$BACKUP_FILE ./$BACKUP_FILE

# 3. Giải phóng /dev/shm trên Secondary Pod
kubectl exec -n $NAMESPACE $TARGET_POD -- rm -f /dev/shm/$BACKUP_FILE
```

#### B. Restore toàn cụm vào Primary Pod
```bash
NAMESPACE="database"
BACKUP_FILE="./rs_full_backup_20261107.dump.gz"
REMOTE_PATH="/dev/shm/rs_restore.dump.gz"

# 1. Tự động truy vấn tìm chính xác tên Pod đang giữ vai trò PRIMARY
PRIMARY_POD=$(kubectl exec -n $NAMESPACE mongo-0 -- mongosh --quiet \
  -u "admin" -p "SecurePass123" --authenticationDatabase "admin" \
  --eval "db.hello().primary.split(':')[0].split('.')[0]" | tr -d '\r\n')

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

# 4. Xóa file khỏi /dev/shm của Primary Pod
kubectl exec -n $NAMESPACE $PRIMARY_POD -- rm -f $REMOTE_PATH

# 5. Kiểm tra trạng thái đồng bộ của các thành viên trong Replica Set
kubectl exec -n $NAMESPACE $PRIMARY_POD -- mongosh --quiet \
  -u "admin" -p "SecurePass123" --authenticationDatabase "admin" \
  --eval "rs.status().members.forEach(m => print(m.name + ' --> ' + m.stateStr))"
```

---

### 5.2. Backup & Restore theo từng Bảng trên Replica Set qua `/dev/shm`

Khi chỉ sao lưu hoặc khôi phục 1 bảng cụ thể (`--db` + `--collection`), ta **bỏ cờ `--oplog`** nhưng vẫn giữ nguyên nguyên tắc: **Lấy từ Secondary, Nạp vào Primary**.

#### A. Xuất riêng 1 Bảng từ Secondary Pod
```bash
NAMESPACE="database"
BACKUP_POD="mongo-1"
TABLE_FILE="orders_table.dump.gz"

# 1. Dump riêng bảng "orders" vào /dev/shm của Secondary
kubectl exec -n $NAMESPACE $BACKUP_POD -- \
  mongodump \
    -u "admin" -p "SecurePass123" --authenticationDatabase "admin" \
    --db="ecommerce_db" \
    --collection="orders" \
    --archive=/dev/shm/$TABLE_FILE \
    --gzip

# 2. Kéo về máy quản trị và dọn dẹp /dev/shm
kubectl cp $NAMESPACE/$BACKUP_POD:/dev/shm/$TABLE_FILE ./$TABLE_FILE
kubectl exec -n $NAMESPACE $BACKUP_POD -- rm -f /dev/shm/$TABLE_FILE
```

#### B. Khôi phục riêng Bảng đó vào Primary Pod (Hoặc dùng Staging + `$merge`)
```bash
# 1. Tự động xác định Primary Pod
PRIMARY_POD=$(kubectl exec -n $NAMESPACE mongo-0 -- mongosh --quiet \
  -u "admin" -p "SecurePass123" --authenticationDatabase "admin" \
  --eval "db.hello().primary.split(':')[0].split('.')[0]" | tr -d '\r\n')

# 2. Copy file vào /dev/shm của PRIMARY Pod
kubectl cp ./$TABLE_FILE $NAMESPACE/$PRIMARY_POD:/dev/shm/$TABLE_FILE

# 3. Trường hợp muốn thay thế toàn bộ bảng orders:
kubectl exec -n $NAMESPACE $PRIMARY_POD -- \
  mongorestore \
    -u "admin" -p "SecurePass123" --authenticationDatabase "admin" \
    --nsInclude="ecommerce_db.orders" \
    --archive=/dev/shm/$TABLE_FILE \
    --gzip \
    --drop

# 4. Dọn dẹp file tạm trên Primary Pod
kubectl exec -n $NAMESPACE $PRIMARY_POD -- rm -f /dev/shm/$TABLE_FILE
```

> 💡 **Nếu muốn Merge/Replace bản dump theo Query trên K8s mà KHÔNG dùng `--drop`:**  
> Thay bước 3 ở trên bằng lệnh restore vào bảng tạm `--nsFrom="ecommerce_db.orders" --nsTo="target_db.orders_staging" --drop`, sau đó chạy `$merge` trực tiếp từ xa:
> ```bash
> kubectl exec -n $NAMESPACE $PRIMARY_POD -- mongosh target_db --quiet \
>   -u "admin" -p "SecurePass123" --authenticationDatabase "admin" \
>   --eval '
>     db.orders_staging.aggregate([{
>       $merge: { into: "orders", on: "_id", whenMatched: "replace", whenNotMatched: "insert" }
>     }]);
>     db.orders_staging.drop();
>   '
> ```

---

## 6. Kỹ Thuật Nâng Cao: UNIX Pipe Streaming (Vượt Giới Hạn RAM `/dev/shm`)

Nếu database hoặc bảng của bạn nặng **50GB – 100GB**, trong khi `/dev/shm` chỉ có 64MB hoặc vài trăm MB RAM, việc lưu file tạm vào `/dev/shm` chắc chắn sẽ làm Pod bị `OOMKilled`.

Giải pháp triệt để là sử dụng **UNIX Pipe Streaming** qua `kubectl exec -i`: Dữ liệu được truyền thẳng từ `stdout`/`stdin` của tiến trình `mongodump`/`mongorestore` qua đường truyền mạng về đĩa cứng của máy quản trị, **không tốn 1 byte dung lượng nào trong `/dev/shm` hay ổ đĩa của Pod**.

### 6.1. Backup toàn bộ Database dạng Stream trực tiếp
```bash
# Không truyền tên file sau cờ --archive để mongodump đẩy luồng dữ liệu ra stdout
kubectl exec -i -n database mongo-1 -- \
  mongodump \
    -u "admin" -p "SecurePass123" --authenticationDatabase "admin" \
    --archive \
    --gzip \
    --oplog > ./backup_direct_stream.dump.gz
```

### 6.2. Xuất 1 Bảng ra CSV dạng Stream trực tiếp
```bash
# Khi không có cờ --out, mongoexport sẽ in kết quả CSV thẳng ra stdout
kubectl exec -i -n database mongo-1 -- \
  mongoexport \
    -u "admin" -p "SecurePass123" --authenticationDatabase "admin" \
    --db="ecommerce_db" \
    --collection="orders" \
    --type=csv \
    --fields="_id,order_code,total_amount,status" > ./orders_stream.csv
```

### 6.3. Restore 1 Bảng dạng Stream trực tiếp vào Primary Pod
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

## 7. Script Tự Động Hóa Backup Trên Production (Kèm `trap` Dọn Dẹp An Toàn)

Khi viết script cho CronJob hoặc CI/CD sử dụng `/dev/shm`, rủi ro lớn nhất là script bị ngắt giữa chừng (do rớt mạng hoặc nhấn `Ctrl+C`) khiến file rác vẫn nằm lại trong `/dev/shm` gây nghẽn RAM của Pod.

Script chuẩn Production dưới đây tự động tìm node Secondary để giảm tải cho Primary và sử dụng cơ chế **`trap cleanup EXIT INT TERM`** để cam kết 100% file tạm trong `/dev/shm` luôn được dọn sạch:

```bash
#!/usr/bin/env bash
set -eo pipefail

NAMESPACE="database"
STS_NAME="mongo"
DB_NAME="ecommerce_db"
COLLECTION_NAME="orders" # Để trống ("") nếu muốn backup toàn bộ DB
MONGO_USER="admin"
MONGO_PASS="SecurePass123"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)

if [ -n "$COLLECTION_NAME" ]; then
  BACKUP_NAME="backup_${DB_NAME}_${COLLECTION_NAME}_${TIMESTAMP}.dump.gz"
  EXTRA_ARGS=(--collection="$COLLECTION_NAME")
else
  BACKUP_NAME="backup_${DB_NAME}_all_${TIMESTAMP}.dump.gz"
  EXTRA_ARGS=()
fi

REMOTE_PATH="/dev/shm/${BACKUP_NAME}"
LOCAL_DEST="./backups/${BACKUP_NAME}"

echo "=== [1/4] Nhận diện Node Secondary để sao lưu ==="
TARGET_POD=$(kubectl exec -n "$NAMESPACE" "${STS_NAME}-0" -- mongosh --quiet \
  -u "$MONGO_USER" -p "$MONGO_PASS" --authenticationDatabase "admin" \
  --eval "
    const status = rs.status();
    if (!status.ok) {
      print('${STS_NAME}-0');
    } else {
      const sec = status.members.find(m => m.stateStr === 'SECONDARY');
      print(sec ? sec.name.split(':')[0].split('.')[0] : '${STS_NAME}-0');
    }
  " | tr -d '\r\n')
echo "--> Pod được chọn: $TARGET_POD"

# Bẫy sự kiện: Đảm bảo dù script thành công, lỗi hay bị Ctrl+C thì /dev/shm vẫn được dọn sạch
cleanup() {
  echo "=== [CLEANUP] Thu hồi RAM: Xóa file tạm trong /dev/shm trên $TARGET_POD ==="
  kubectl exec -n "$NAMESPACE" "$TARGET_POD" -- rm -f "$REMOTE_PATH" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

echo "=== [2/4] Thực hiện mongodump vào /dev/shm ==="
kubectl exec -n "$NAMESPACE" "$TARGET_POD" -- \
  mongodump \
    -u "$MONGO_USER" -p "$MONGO_PASS" --authenticationDatabase "admin" \
    --db="$DB_NAME" \
    "${EXTRA_ARGS[@]}" \
    --archive="$REMOTE_PATH" \
    --gzip

echo "=== [3/4] Copy file về máy chủ lưu trữ ==="
mkdir -p "$(dirname "$LOCAL_DEST")"
kubectl cp "$NAMESPACE/$TARGET_POD:$REMOTE_PATH" "$LOCAL_DEST"
echo "--> Đã lưu thành công tại: $LOCAL_DEST"

echo "=== [4/4] Hoàn tất sao lưu an toàn! ==="
```

---

## 8. Bảng Tra Cứu Nhanh: Khi Nào Dùng Giải Pháp Nào?

| Tình huống thực tế | Công cụ & Cờ khuyên dùng | Phương thức trên Kubernetes |
| :--- | :--- | :--- |
| **Backup toàn cụm Replica Set (Point-in-time)** | `mongodump --archive --gzip --oplog` | Chạy trên **Secondary Pod** (qua Streaming Pipe nếu DB > 50MB) |
| **Backup / Restore 1 bảng nhỏ (< 50MB)** | `mongodump -d <db> -c <col>` / `mongorestore --nsInclude` | Qua `/dev/shm` + `kubectl cp` + `rm -f` ngay sau khi xong |
| **Backup / Restore DB lớn (hàng chục GB)** | `mongodump --archive --gzip` | Dùng **UNIX Pipe Streaming** (`kubectl exec -i ... > file.dump.gz`) |
| **Lọc dữ liệu theo điều kiện phức tạp (`$date`, `$gte`)** | `mongodump` / `mongoexport` kèm `--queryFile` | Tạo file `filter.json` trong `/dev/shm` để tránh lỗi escape ký tự `$` |
| **Nạp bản dump BSON theo Query (Không xóa dữ liệu cũ)** | `mongorestore --nsFrom --nsTo` + Aggregation `$merge` | Restore vào bảng `*_staging` trên **Primary Pod**, chạy `$merge` rồi drop bảng tạm |
| **Xuất báo cáo cho Excel / BI hoặc Upsert từ CSV** | `mongoexport --type=csv` / `mongoimport --mode=upsert` | Xuất từ **Secondary Pod**, nhập vào **Primary Pod** |

---

## Tổng Kết & Bước Đệm Sang Phần 5

- Luôn ưu tiên **`mongodump` / `mongorestore`** cho công tác Disaster Recovery để bảo toàn 100% kiểu dữ liệu BSON và Indexes. Chỉ dùng **`mongoexport` / `mongoimport`** khi cần làm việc với CSV/JSON.
- Khi thao tác với dữ liệu lọc theo query, **tuyệt đối không dùng `--drop`** trực tiếp lên bảng chính; hãy sử dụng kỹ thuật **Staging Collection + `$merge`**.
- Trên Kubernetes có bật `readOnlyRootFilesystem: true`, **`/dev/shm`** là vùng nhớ tạm tiện lợi nhưng luôn phải đi kèm cơ chế xóa file (`trap`) hoặc chuyển sang **UNIX Pipe Streaming** với dữ liệu lớn để tránh `OOMKilled`.
- Nguyên tắc bất di bất dịch trên Replica Set: **Xuất từ Secondary, Nhập vào Primary**.

👉 **Đón đọc bài cuối cùng (Phần 5):** [**Giám Sát, Bảo Mật & Disaster Recovery Nâng Cao**](/posts/mongodb-part5-monitoring-security-disaster-recovery/) — Triển khai Prometheus `mongodb-exporter`, mã hóa đường truyền TLS/mTLS, cô lập mạng với `NetworkPolicy` và kỹ thuật chụp CSI Volume Snapshot cho dữ liệu hàng Terabytes trong tích tắc.
