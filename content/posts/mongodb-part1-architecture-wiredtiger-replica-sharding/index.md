---
title: "MongoDB Thực Chiến (Phần 1): Đi Sâu Vào Kiến Trúc – Từ WiredTiger Engine Đến Replica Set & Sharding"
date: 2026-11-07T14:00:00+07:00
draft: false
description: "Bài mở đầu series MongoDB Thực Chiến. Khám phá toàn diện kiến trúc bên dưới của MongoDB: Cơ chế WiredTiger Storage Engine (Cache, Checkpoint, Journal WAL), mô hình độ sẵn sàng cao Replica Set (Election, Oplog, Quorum) và mở rộng quy mô lớn với Sharded Cluster."
summary: "Đi sâu vào kiến trúc MongoDB từ tầng thấp nhất: Bộ nhớ đệm WiredTiger Cache, chống mất dữ liệu với Journaling, cơ chế bầu cử Replica Set và cách phân mảnh Sharded Cluster tránh điểm nghẽn."
tags: ["MongoDB", "Database", "DevOps", "Architecture", "NoSQL", "High-Availability", "Performance"]
categories: ["Database", "DevOps", "Architecture"]
series: ["MongoDB Thực Chiến"]
series_order: 1
showTableOfContents: true
---

Chào mừng bạn đến với chuỗi bài viết **"MongoDB Thực Chiến: Từ Kiến Trúc Đến Vận Hành Trên Kubernetes"**!

Khi làm việc với MongoDB, rất nhiều lập trình viên chỉ xem nó như một kho chứa JSON tiện lợi ("lưu gì cũng được, không cần migration schema"). Tuy nhiên, khi hệ thống chạm mốc hàng chục triệu bản ghi, hàng ngàn request/giây hoặc đối mặt với sự cố hạ tầng, việc không hiểu rõ **bản chất kiến trúc bên dưới** sẽ dẫn đến nghẽn I/O, mất mát dữ liệu ngầm hoặc thời gian chết (downtime) ngoài ý muốn.

Trong bài viết mở đầu này, chúng ta sẽ "mổ xẻ" MongoDB từ tầng thấp nhất (**Storage Engine WiredTiger**) cho đến các mô hình phân tán quy mô lớn (**Replica Set** và **Sharding**).

---

## 1. Trái tim của MongoDB: WiredTiger Storage Engine

Kể từ phiên bản 3.2, **WiredTiger** trở thành storage engine mặc định của MongoDB. Hiểu cách WiredTiger hoạt động sẽ giải thích tại sao MongoDB lại chiếm dụng nhiều RAM và cách nó bảo vệ an toàn cho dữ liệu của bạn.

{{< mermaid >}}
flowchart TB
    App["🖥️ Ứng Dụng (Application)"] -->|Write Request| Engine

    subgraph Engine["⚙️ WiredTiger Storage Engine"]
        Cache["💾 WiredTiger Cache (RAM)<br/>Dirty Pages (Dữ liệu chưa ghi đĩa)"]
        JBuf["⚡ Journal Buffer (RAM)<br/>Lưu thao tác thay đổi"]
    end

    subgraph Disk["💿 Lưu Trữ Ổ Cứng (Disk)"]
        DataFiles[("📁 Data Files (.wt)<br/>Nén Snappy / zlib")]
        JournalFiles[("📝 Journal Files (WAL)<br/>Ghi tuần tự để phục hồi")]
    end

    Cache -->|Checkpoint mỗi 60s| DataFiles
    JBuf -->|fsync flush mỗi 100ms| JournalFiles
{{< /mermaid >}}

### 1.1. Bộ nhớ đệm (WiredTiger Cache)
Theo mặc định, WiredTiger sẽ chiếm dụng lượng RAM vật lý theo công thức:

$$\text{RAM cho Cache} = 50\% \times (\text{Tổng RAM hệ thống} - 1\text{ GB})$$
*(tối thiểu là 256MB)*.

Ví dụ: Nếu máy chủ của bạn có 16GB RAM, WiredTiger Cache sẽ lấy xấp xỉ `0.5 * (16 - 1) = 7.5GB` RAM. Phần RAM còn lại dành cho hệ điều hành (OS Page Cache), kết nối mạng (Connection Buffers) và các tiến trình khác.

* Khi ứng dụng thực hiện `insert` hoặc `update`, dữ liệu **chưa được ghi ngay xuống file data chính trên đĩa cứng**. Nó được nạp vào RAM dưới dạng **Dirty Pages**.
* Khi đọc dữ liệu (Read), MongoDB ưu tiên đọc trực tiếp từ Cache. Nếu Cache miss, nó mới đọc từ đĩa nạp lên RAM.

### 1.2. Checkpoint và Journaling (Cơ chế chống mất dữ liệu khi sập nguồn)
Nếu máy chủ bị mất điện đột ngột trong khi dữ liệu mới vẫn nằm trên RAM thì sao? MongoDB giải quyết bài toán này thông qua 2 cơ chế phối hợp:

1. **Checkpoint (Mặc định 60 giây một lần):**
   * Cứ mỗi 60 giây (hoặc khi dung lượng journal log đạt 2GB), WiredTiger sẽ gom toàn bộ dirty pages trên RAM và ghi đồng loạt xuống file data chính (`.wt`). Snapshot này thể hiện trạng thái nhất quán hoàn toàn của dữ liệu trên đĩa.
2. **Journal (Write-Ahead Logging - WAL):**
   * Trong khoảng thời gian 60 giây giữa 2 lần Checkpoint, mọi thao tác ghi đều được ghi tuần tự vào **Journal Files** (mặc định flush xuống đĩa mỗi **100ms**).
3. **Cơ chế Crash Recovery:**
   * Khi server khởi động lại sau sự cố sập nguồn:
     1. MongoDB nạp dữ liệu từ **Checkpoint gần nhất**.
     2. Đọc **Journal Files** và replay (chạy lại) toàn bộ các thao tác phát sinh sau Checkpoint đó.
     3. Dữ liệu trở về trạng thái toàn vẹn mà không bị hỏng hóc (corruption).

---

## 2. Mô hình Tính Sẵn Sàng Cao: Replica Set (High Availability)

Trong môi trường Production, **không bao giờ** chạy MongoDB ở dạng Standalone (đơn node). Tiêu chuẩn bắt buộc là một **Replica Set gồm ít nhất 3 node** để loại bỏ hoàn toàn điểm nghẽn đơn lẻ (Single Point of Failure - SPOF).

{{< mermaid >}}
flowchart TB
    App["🖥️ Ứng Dụng (Application Driver)"]
    
    subgraph RS["☸️ MongoDB Replica Set (rs0)"]
        Primary["⭐ PRIMARY NODE<br/>(Tiếp nhận mọi lệnh GHI & ĐỌC chính)"]
        Secondary1["🔄 SECONDARY NODE 1<br/>(Đồng bộ dữ liệu qua Oplog)"]
        Secondary2["🔄 SECONDARY NODE 2<br/>(Đồng bộ dữ liệu qua Oplog)"]
    end

    App -->|Write Requests| Primary
    App -.->|Read Requests (Tùy chọn)| Secondary1
    App -.->|Read Requests (Tùy chọn)| Secondary2

    Primary -->|Oplog Stream| Secondary1
    Primary -->|Oplog Stream| Secondary2
    Secondary1 <-->|Heartbeat (2s)| Primary
    Secondary2 <-->|Heartbeat (2s)| Primary
    Secondary1 <-->|Heartbeat (2s)| Secondary2
{{< /mermaid >}}

### 2.1. Các vai trò trong Replica Set
1. **Primary Node:** Node duy nhất tiếp nhận các lệnh **GHI (Write)** từ ứng dụng.
2. **Secondary Node:** Đồng bộ dữ liệu liên tục từ Primary. Có thể phục vụ các lệnh **ĐỌC (Read)** nếu ứng dụng cấu hình Read Preference.
3. **Arbiter:** Một node chỉ tham gia biểu quyết bầu cử, **không chứa dữ liệu**.
   * ⚠️ *Khuyến cáo Production:* **Hạn chế tối đa việc sử dụng Arbiter**. Hãy sử dụng mô hình chuẩn **3 Data Nodes (1 Primary, 2 Secondary)**. Dùng Arbiter có thể làm nghẽn Oplog hoặc gây mất mát dữ liệu khi mạng bị phân mảnh (Network Partition).

### 2.2. Oplog (Operations Log) – Xương sống của sự đồng bộ
* Oplog là một **Capped Collection** đặc biệt (`local.oplog.rs`) nằm trên Primary.
* Oplog ghi lại toàn bộ thao tác thay đổi dữ liệu dưới dạng **idempotent** (thao tác lặp lại nhiều lần vẫn cho ra cùng một kết quả). Ví dụ: lệnh tăng view `{$inc: {views: 1}}` sẽ được Oplog ghi thành kết quả cố định `{$set: {views: 42}}`.
* Các node Secondary liên tục lắng nghe và kéo (pull) Oplog này về để áp dụng tuần tự vào dữ liệu của mình.

### 2.3. Cơ chế Bầu cử (Election) & Quorum
* Các node gửi tín hiệu **Heartbeat mỗi 2 giây** cho nhau.
* Nếu Secondary không nhận được phản hồi từ Primary trong vòng **10 giây** (mặc định), nó sẽ khởi tạo quy trình bầu cử để tìm Primary mới.
* **Nguyên tắc Quorum (Đa số tuyệt đối):** Một node chỉ có thể đắc cử Primary nếu nhận được sự đồng thuận của đa số thành viên:

$$\text{Số phiếu tối thiểu} = \left\lfloor \frac{N}{2} \right\rfloor + 1$$

Với cụm 3 node, cần ít nhất $2$ phiếu. Nếu một node bị cô lập mạng (chỉ còn 1 node sống sót), node đó sẽ tự động giáng cấp thành Secondary để tránh hiện tượng **Split-Brain** (hai node cùng tưởng mình là Primary).

---

## 3. Bản chất của Write Concern & Read Concern

Hiểu rõ hai cơ chế này giúp kỹ sư kiểm soát được sự cân bằng giữa **Hiệu năng (Performance)** và **An toàn dữ liệu (Consistency)**.

### 3.1. Write Concern (`w`)
Mô tả mức độ xác nhận từ MongoDB trước khi thông báo ghi thành công về cho ứng dụng:

* `w: 1` *(Mặc định trước bản 5.0)*: Chỉ cần node **Primary** ghi vào RAM/Cache là trả về thành công ngay.
  * ❌ *Rủi ro:* Nếu Primary đột ngột sập trước khi kịp sync sang Secondary, dữ liệu đó sẽ bị mất (Rollback).
* `w: "majority"` *(Mặc định từ bản 5.0 trở đi)*: Lệnh ghi phải được xác nhận bởi **đa số** node trong cụm (ví dụ: ít nhất 2 trên 3 node).
  * ✅ *An toàn tuyệt đối:* Dữ liệu không bao giờ bị rollback khi failover.
* `j: true` (Journaling): Bắt buộc dữ liệu phải được flush xuống file Journal trên đĩa cứng rồi mới trả về thành công.

```javascript
// Ví dụ: Ghi dữ liệu giao dịch tài chính an toàn tuyệt đối
db.orders.insertOne(
  { orderId: "ORD-99999", amount: 1500000, status: "PAID" },
  { writeConcern: { w: "majority", j: true, wtimeout: 5000 } }
);
```

### 3.2. Read Preference & Read Concern
* **Read Preference:** Quyết định driver sẽ chuyển câu query đọc tới node nào:
  * `primary` (Mặc định): Luôn đọc từ Primary (đảm bảo thấy dữ liệu mới nhất).
  * `secondary`: Đọc từ Secondary để giảm tải cho Primary (chấp nhận dữ liệu có độ trễ vài mili-giây - Eventual Consistency).
  * `secondaryPreferred`: Ưu tiên Secondary, nếu tất cả Secondary chết thì mới đọc từ Primary.

* **Read Concern:** Kiểm soát tính nhất quán của dữ liệu đọc ra:
  * `local`: Trả về dữ liệu hiện có trên node, không cần biết dữ liệu đã được ghi vào đa số node hay chưa.
  * `majority`: Chỉ trả về dữ liệu đã được commit thành công trên đa số các node (tránh đọc phải dữ liệu ma - Dirty Read).
  * `linearizable`: Đợi Primary xác nhận với các node khác rằng nó vẫn là Primary hợp lệ trước khi trả data (ngăn chặn Stale Read khi mạng bị phân mảnh).

---

## 4. Mô hình Mở Rộng Quy Mô Lớn: Sharded Cluster

Khi dữ liệu vượt qua ngưỡng vài Terabyte hoặc số lượng request ghi vượt quá khả năng xử lý của một cụm Replica Set đơn lẻ, bạn bắt buộc phải **Scale Out (mở rộng ngang)** bằng **Sharding**.

{{< mermaid >}}
flowchart TB
    App["🖥️ Ứng Dụng (Application)"] -->|Cổng 27017| Mongos

    Mongos["🧭 mongos (Query Router)<br/>Stateless, có thể mở rộng nhiều pod"]

    subgraph ConfigCluster["⚙️ Config Database (Replica Set)"]
        ConfigRS[("Lưu trữ Metadata & Bản đồ phân bố Chunks")]
    end

    subgraph Shards["📦 Các Shards (Mỗi Shard là 1 Replica Set độc lập)"]
        Shard1[("Shard 1<br/>Chứa dải dữ liệu A - M")]
        Shard2[("Shard 2<br/>Chứa dải dữ liệu N - Z")]
    end

    Mongos <-->|Tra cứu Metadata| ConfigCluster
    Mongos -->|Chuyển hướng chính xác| Shard1
    Mongos -->|Chuyển hướng chính xác| Shard2
{{< /mermaid >}}

### 4.1. Ba thành phần cốt lõi của Sharded Cluster
1. **Shards (Các mảnh dữ liệu):** Mỗi Shard lưu trữ một phần dữ liệu của cluster. Trong môi trường Production, **mỗi Shard bắt buộc phải là một Replica Set riêng biệt**.
2. **Config Servers:** Lưu trữ metadata của cụm và bản đồ chỉ rõ dữ liệu nào (Chunk nào) đang nằm ở Shard nào. Bắt buộc là một Replica Set gồm 3 node.
3. **Mongos (Query Routers):** Các tiến trình định tuyến nhẹ (stateless). Ứng dụng kết nối tới `mongos` giống hệt như kết nối tới một node mongod bình thường. `mongos` sẽ tra cứu Config Servers để biết cần chuyển câu query tới Shard nào.

### 4.2. Chiến lược chọn Shard Key – "Sống hay Chết" của Sharding
Shard Key là một hoặc nhiều trường trong document quyết định document đó sẽ nằm ở Shard nào. Có 2 cách chia Shard chính:

#### Cách 1: Ranged Sharding (Chia theo khoảng giá trị)
Dữ liệu được chia theo các dải giá trị liên tiếp (ví dụ: `age: 1 - 20` vào Shard 1, `21 - 40` vào Shard 2).
* 👍 *Ưu điểm:* Tối ưu cực tốt cho các câu truy vấn dạng khoảng (`find({ age: { $gt: 10, $lt: 18 } })`). `mongos` chỉ cần trỏ thẳng tới Shard 1.
* 👎 *Nhược điểm:* Dễ sinh ra **Hotspotting** (Nghẽn cổ chai) nếu chọn key tăng tự tiến (như `ObjectId`, `createdAt`). Toàn bộ lượt ghi dữ liệu mới sẽ dồn hết vào shard cuối cùng!

#### Cách 2: Hashed Sharding (Băm giá trị)
MongoDB tính mã băm MD5 của Shard Key và phân bổ đều khắp các Shard.
* 👍 *Ưu điểm:* Dữ liệu và tải ghi (Write load) được phân bổ **cực kỳ đồng đều** giữa các Shard.
* 👎 *Nhược điểm:* Câu truy vấn theo khoảng sẽ biến thành **Scatter-Gather** (`mongos` phải broadcast query tới tất cả các Shard rồi gom kết quả lại, làm tăng độ trễ mạng).

```javascript
// Bật sharding cho Database
sh.enableSharding("ecommerce_db");

// Tạo Hashed Shard Key trên collection users theo trường user_id
sh.shardCollection("ecommerce_db.users", { "user_id": "hashed" });
```

---

## 5. Bảng so sánh tổng kết: Chọn mô hình nào?

| Tiêu chí | Standalone | Replica Set | Sharded Cluster |
| :--- | :--- | :--- | :--- |
| **High Availability (Sẵn sàng cao)** | ❌ Không (Sập là chết) | ✅ Tự động Failover | ✅ HA toàn diện trên từng shard |
| **Dung lượng tối đa** | Bị giới hạn bởi 1 ổ cứng | Bị giới hạn bởi ổ cứng 1 node | 🚀 Mở rộng không giới hạn (Petabytes) |
| **Write Throughput** | Giới hạn ở 1 node | Vẫn giới hạn ở node Primary | 🚀 Phân tải ghi ra N Shard |
| **Độ phức tạp vận hành** | Rất thấp (1 process) | Trung bình (Dễ bảo trì) | Cao (Cần quản lý nhiều cụm RS, Router) |
| **Môi trường phù hợp** | Local Dev / CI Test | **95% ứng dụng Production** | Hệ thống Big Data / Siêu ứng dụng |

---

## Tóm tắt & Bước đệm sang Bài 2

* WiredTiger cân bằng giữa tốc độ ghi trên RAM và độ bền vững trên đĩa nhờ **Cache + Checkpoint + Journal**.
* **Replica Set** là tiêu chuẩn tối thiểu cho Production, cung cấp khả năng tự động failover và đồng bộ qua **Oplog**.
* Dùng **Write Concern `majority`** để tránh mất dữ liệu khi chuyển dịch node Primary.
* Chỉ triển khai **Sharding** khi dung lượng đĩa hoặc thông lượng ghi chạm ngưỡng giới hạn của 1 cụm Replica Set.

👉 **Ở bài viết tiếp theo (Bài 2):** Chúng ta sẽ đi sâu vào tầng ứng dụng: **"Thiết Kế Schema & Kỹ Thuật Indexing Tối Ưu Hiệu Năng Trong MongoDB"** — Làm thế nào để chọn giữa Embedding và Referencing? Tối ưu Compound Index với quy tắc ESR (Equality, Sort, Range) để chặn đứng các câu truy vấn chậm (Slow Queries).
