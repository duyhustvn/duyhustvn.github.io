---
title: "MongoDB Thực Chiến (Phần 2): Thiết Kế Schema & Kỹ Thuật Indexing Tối Ưu Hiệu Năng"
date: 2026-11-07T15:00:00+07:00
draft: false
description: "Làm chủ tư duy thiết kế schema NoSQL: Lựa chọn sống còn giữa Embedding vs Referencing, giải quyết bài toán Document Growth và giới hạn 16MB. Nắm vững nghệ thuật Indexing với quy tắc vàng ESR (Equality, Sort, Range) và giải mã explain executionStats."
summary: "Từ bỏ tư duy chuẩn hóa RDBMS để làm chủ Data Modeling trong MongoDB: Áp dụng Subset Pattern, Bucket Pattern, tối ưu Compound Index với quy tắc ESR và loại bỏ triệt để COLLSCAN."
tags: ["MongoDB", "Database", "Schema-Design", "Indexing", "Performance", "DevOps", "Backend"]
categories: ["Database", "Performance"]
series: ["MongoDB Thực Chiến"]
series_order: 2
showTableOfContents: true
---

Trong thế giới cơ sở dữ liệu quan hệ (RDBMS), việc thiết kế bảng thường tuân thủ quy tắc chuẩn hóa (Third Normal Form - 3NF) nhằm loại bỏ trùng lặp dữ liệu. Nhưng với MongoDB, triết lý thiết kế hoàn toàn trái ngược: **Thiết kế cấu trúc dữ liệu theo cách mà ứng dụng truy vấn (Query-Driven Design)**.

Nếu mang nguyên tư duy RDBMS sang MongoDB bằng cách tạo ra hàng chục collection nhỏ rồi dùng `$lookup` (tương đương `JOIN`) để gom dữ liệu lại, hệ thống của bạn sẽ nhanh chóng đối mặt với tình trạng nghẽn CPU và độ trễ truy vấn tăng vọt.

---

## 1. Quyết định cốt lõi: Nhúng (Embedding) hay Tham chiếu (Referencing)?

Mọi quyết định thiết kế schema trong MongoDB đều xoay quanh một câu hỏi: **Gom chung dữ liệu con vào 1 Document duy nhất hay tách ra thành các Collection riêng biệt và liên kết qua `_id`?**

```
EMBEDDING (Nhúng trực tiếp vào 1 Document)
+───────────────────────────────────────────────────────────+
| User Document                                             |
| - _id: ObjectId("...")                                    |
| - name: "Nguyễn Văn A"                                    |
| - addresses: [                                            |
|     { street: "123 Cầu Giấy", city: "Hà Nội" },          |
|     { street: "456 Lê Lợi", city: "Đà Nẵng" }             |
|   ]                                                       |
+───────────────────────────────────────────────────────────+

REFERENCING (Tách thành các Collection riêng biệt)
+───────────────────────────+       +───────────────────────────────────+
| User Document             |       | Order Document                    |
| - _id: ObjectId("usr_1")  | <───┐ | - _id: ObjectId("ord_101")        |
| - name: "Nguyễn Văn A"    |     └─┼── user_id: ObjectId("usr_1")      |
+───────────────────────────+       | - total_amount: 1500000           |
                                    +───────────────────────────────────+
```

### 1.1. Bảng quy tắc lựa chọn:

| Tiêu chí so sánh | Nhúng (Embedding) | Tham chiếu (Referencing) |
| :--- | :--- | :--- |
| **Bản chất mối quan hệ** | **1 : 1** hoặc **1 : Few** (Ít dữ liệu con, ví dụ: 1 user có 2-3 địa chỉ) | **1 : Many** hoặc **1 : Squillions** (1 user có hàng chục ngàn đơn hàng/logs) |
| **Cách ứng dụng đọc dữ liệu** | Dữ liệu cha và con **luôn được đọc cùng lúc** trong 1 màn hình | Dữ liệu con thường được đọc riêng rẽ, có phân trang hoặc lọc độc lập |
| **Tần suất cập nhật** | Dữ liệu con ít khi bị sửa đổi | Dữ liệu con liên tục được thêm mới, cập nhật |
| **Kích thước Document** | Chắc chắn không bao giờ chạm ngưỡng **16MB** | Tránh rủi ro Document phình to vượt quá 16MB |

### 1.2. Mối nguy hiểm: "Document Growth" & Phân mảnh bộ nhớ
Khi một Document được ghi xuống đĩa, WiredTiger sẽ phân bổ một khối nhớ liên tục vừa vặn với kích thước ban đầu của nó. 
* Nếu bạn liên tục `$push` dữ liệu vào một mảng nhúng (ví dụ: nhúng toàn bộ lịch sử xem trang của user), Document đó sẽ vượt quá kích thước block nhớ cũ.
* WiredTiger sẽ buộc phải di dời toàn bộ Document đó sang một vùng nhớ mới trên đĩa cứng, để lại một "lỗ hổng" trống rỗng.
* Hiện tượng này gọi là **Memory & Disk Fragmentation** (Phân mảnh bộ nhớ), khiến kích thước database phình to giả tạo và làm chậm hiệu năng ghi.

---

## 2. Các Design Patterns thực chiến trong MongoDB

### 2.1. Subset Pattern (Giải quyết bài toán 100.000 bình luận)
*   **Vấn đề:** Trong ứng dụng mạng xã hội hoặc thương mại điện tử, 99% người dùng khi vào xem bài viết chỉ đọc 5-10 bình luận mới nhất. Tuy nhiên, bài viết có thể có tới 50.000 bình luận. Nếu nhúng hết vào thì Document sẽ vượt quá 16MB; nếu tách riêng ra thì mỗi lần mở trang lại tốn thêm 1 câu query `$lookup`.
*   **Giải pháp:** Áp dụng **Subset Pattern** — Chỉ nhúng 5-10 bình luận mới nhất vào chính document bài viết, còn toàn bộ 50.000 bình luận đầy đủ vẫn nằm ở collection `comments` riêng biệt.

```json
// Collection: articles (Chứa 10 bình luận mới nhất để render tức thì)
{
  "_id": ObjectId("6501a111..."),
  "title": "Kiến Trúc Microservices Với Kubernetes",
  "comment_count": 48200,
  "recent_comments": [
    { "user": "nam_dev", "text": "Bài viết cực kỳ trực quan!", "created_at": ISODate("...") },
    { "user": "lan_ops", "text": "Cảm ơn tác giả nhiều nhé", "created_at": ISODate("...") }
  ]
}
```

### 2.2. Bucket Pattern (Xử lý dữ liệu Time-Series / IoT / Lịch sử cảm biến)
*   **Vấn đề:** Một thiết bị cảm biến nhiệt độ gửi dữ liệu mỗi giây 1 lần. Sau 1 tháng, bạn có hàng triệu documents nhỏ lẻ. Việc đánh index trên hàng chục triệu bản ghi này sẽ "nuốt chửng" toàn bộ RAM của server.
*   **Giải pháp:** Gom cụm toàn bộ số lần đo trong 1 giờ vào một Document duy nhất (Bucket):

```json
// Collection: sensor_buckets
{
  "sensor_id": "TEMP-RACK-01",
  "bucket_start": ISODate("2026-11-07T08:00:00Z"),
  "count": 3600,
  "readings": [
    { "sec": 0, "temp": 24.5 },
    { "sec": 1, "temp": 24.6 },
    { "sec": 3599, "temp": 25.1 }
  ]
}
```
*Số lượng document giảm 3600 lần, kích thước Index giảm ngoạn mục!*

---

## 3. Nghệ Thuật Indexing & Quy Tắc Vàng ESR

Chỉ số (Index) trong MongoDB được xây dựng dựa trên cấu trúc cây tìm kiếm nhị phân cân bằng **B-Tree**. Nếu không có Index, MongoDB bắt buộc phải thực hiện quét toàn bộ bảng (`COLLSCAN`), dẫn đến 100% CPU spike và làm nghẽn toàn bộ hệ thống.

### 3.1. Quy tắc ESR (Equality, Sort, Range)
Khi cần tạo Compound Index (Index nhiều trường) để phục vụ một câu truy vấn phức tạp vừa có lọc bằng, vừa sắp xếp, vừa lọc theo khoảng giá trị, bạn **bắt buộc phải đặt thứ tự các trường theo đúng công thức**:

{{< mermaid >}}
flowchart LR
    E["1. Equality (E)<br/>Lọc chính xác (=, $eq)"] --> S["2. Sort (S)<br/>Sắp xếp (.sort)"]
    S --> R["3. Range (R)<br/>Lọc khoảng ($gt, $lt, $in)"]
{{< /mermaid >}}

#### Ví dụ bài toán thực tế:
Bạn có câu query tìm kiếm đơn hàng:
```javascript
db.orders.find({
  status: "COMPLETED",            // [E] Equality: Lọc chính xác giá trị
  total: { $gte: 200, $lte: 800 } // [R] Range: Lọc theo khoảng
}).sort({ order_date: -1 })        // [S] Sort: Sắp xếp giảm dần theo ngày
```

#### So sánh giữa cách đặt Index sai và đúng:

❌ **Sai lầm phổ biến (Đặt index theo thứ tự xuất hiện trong query):**
```javascript
db.orders.createIndex({ status: 1, total: 1, order_date: -1 })
```
*Kết quả:* MongoDB dùng index để lọc `status` và `total`. Sau đó, nó nhận về hàng ngàn document nằm rải rác và **buộc phải dùng RAM để thực hiện sắp xếp (In-memory Sort)**. Nếu tập dữ liệu vượt quá 100MB RAM, câu query sẽ sập ngay lập tức với lỗi `Sort exceeded memory limit`.

✅ **Chuẩn mực theo quy tắc ESR:**
```javascript
db.orders.createIndex({
  status: 1,       // [E] Equality đặt ĐẦU TIÊN
  order_date: -1,  // [S] Sort đặt Ở GIỮA
  total: 1         // [R] Range đặt CUỐI CÙNG
})
```
*Kết quả:* MongoDB nhảy thẳng đến nhánh `status = "COMPLETED"`, duyệt cây B-Tree theo thứ tự ngày tháng đã được sắp xếp sẵn trên đĩa, và chỉ việc lọc lấy các bản ghi có `total` nằm trong khoảng yêu cầu. Không tốn 1 byte RAM nào cho việc sort!

---

## 4. Các Loại Index Chuyên Dụng Cần Biết

### 4.1. Partial Index (Index một phần)
Chỉ đánh index trên các document thỏa mãn một điều kiện nhất định, giúp tiết kiệm 70-80% dung lượng RAM:
```javascript
// Chỉ đánh index trên những đơn hàng ĐÃ HOÀN TẤT
db.orders.createIndex(
  { customer_id: 1, order_date: -1 },
  { partialFilterExpression: { status: "COMPLETED" } }
)
```

### 4.2. TTL Index (Time-To-Live – Tự Động Xóa Dữ Liệu Hết Hạn)
Cực kỳ lý tưởng cho các bảng lưu OTP, Session đăng nhập hoặc Log tạm thời:
```javascript
// Tự động xóa document sau 3600 giây (1 giờ) tính từ thời điểm createdAt
db.user_sessions.createIndex(
  { "createdAt": 1 },
  { expireAfterSeconds: 3600 }
)
```

---

## 5. Giải Mã `explain("executionStats")`

Đừng bao giờ đoán mò hiệu năng! Hãy dùng `explain("executionStats")` để xem chi tiết cách MongoDB thực thi câu truy vấn:

```javascript
db.orders.find({
  status: "COMPLETED",
  total: { $gte: 200, $lte: 800 }
}).sort({ order_date: -1 }).explain("executionStats")
```

### 3 chỉ số then chốt cần kiểm tra:

1. **`stage`:**
   * ✅ **Tốt:** `IXSCAN` (Index Scan) kết hợp `FETCH`.
   * ❌ **Xấu:** `COLLSCAN` (Quét toàn bộ collection vì thiếu index).
2. **Tỷ lệ `totalDocsExamined` / `nReturned`:**
   * Tỷ lệ vàng lý tưởng là xấp xỉ **1:1** (Đọc 10 document từ đĩa và trả về đúng 10 kết quả cho người dùng).
   * Nếu tỷ lệ này lên đến 10.000 / 10 $\rightarrow$ Index của bạn đang lọc rất kém, làm lãng phí I/O ổ cứng.
3. **`sortStage`:**
   * Nếu trong kết quả xuất hiện `SORT` hoặc `SORT_KEY_GENERATOR`, nghĩa là bạn đã vi phạm quy tắc ESR và MongoDB đang phải sort trên bộ nhớ RAM.

---

## Tóm tắt & Bước đệm sang Bài 3

* Luôn thiết kế cấu trúc dữ liệu theo cách mà ứng dụng truy vấn (**Query-Driven**).
* Chọn **Embedding** cho mối quan hệ 1:Few; chọn **Referencing** cho mối quan hệ 1:Many không giới hạn để tránh lỗi 16MB Document Limit.
* Áp dụng **Subset Pattern** và **Bucket Pattern** cho các bài toán dữ liệu lớn.
* Khắc cốt ghi tâm **Quy tắc ESR (Equality $\rightarrow$ Sort $\rightarrow$ Range)** khi tạo Compound Index.

👉 **Ở bài viết tiếp theo (Bài 3):** Chúng ta sẽ đưa toàn bộ lý thuyết vào môi trường thực tế: **"Triển Khai & Vận Hành MongoDB Replica Set Chuẩn Production Trên Kubernetes"** — Cấu hình StatefulSet, Headless Service, xử lý Internal Auth với Keyfile và tự động hóa Bootstrap cluster.
