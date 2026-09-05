---
title: "Làm chủ lệnh ss (Socket Statistics): Điều tra và xử lý sự cố mạng chuyên sâu trên Linux & Kubernetes"
date: 2026-09-05T13:00:00+07:00
draft: false
description: "Hướng dẫn toàn diện từ cơ bản đến chuyên sâu về lệnh ss (Socket Statistics) trong Linux: Bản chất kiến trúc Netlink API, giải mã ý nghĩa Recv-Q/Send-Q cho LISTEN vs ESTABLISHED, bộ lọc nâng cao và các kịch bản thực chiến xử lý CLOSE-WAIT leak, TIME-WAIT storm, nghẽn hàng đợi TCP."
summary: "Khám phá sức mạnh tối thượng của ss (Socket Statistics) thay thế hoàn toàn netstat: Cơ chế Netlink sock_diag trong Linux kernel, bí mật giải mã Recv-Q & Send-Q, và cẩm nang xử lý sự cố mạng Production trên Linux & Kubernetes."
tags: ["Linux", "Networking", "ss", "Troubleshooting", "DevOps", "TCP/IP", "Kubernetes", "Performance", "Sysadmin"]
categories: ["Networking & DevOps", "Linux", "Troubleshooting"]
showTableOfContents: true
---

Trong môi trường Production hiện đại với kiến trúc Microservices, Distributed Systems và Kubernetes, **kết nối mạng (Network Sockets)** là huyết mạch kết nối giữa các thành phần. Tuy nhiên, các sự cố mạng lại thường là những vấn đề "nhức nhối" nhất đối với các kỹ sư DevOps, SRE và Backend:

- Một service bất ngờ phản hồi chậm trễ, client liên tục gặp lỗi `Connection timed out`.
- Hệ thống bị rò rỉ socket âm thầm, dẫn đến thảm họa `Too many open files (EMFILE)` và đánh sập ứng dụng.
- Số lượng kết nối `TIME-WAIT` bùng nổ làm cạn kiệt dải ephemeral ports, khiến service không thể gọi ra Database hoặc External API (`Cannot assign requested address`).
- Hàng đợi nhận gói tin bị tràn khiến các kết nối TCP mới bị drop không dấu vết.

Khi những sự cố này xảy ra, bạn không thể ngồi đoán mò. Bạn cần một công cụ soi sáng chính xác trạng thái của từng socket trong kernel.

Trong quá khứ, công cụ quen thuộc là `netstat`. Nhưng từ hơn một thập kỷ nay, `netstat` đã bị đánh dấu **deprecated** (lỗi thời) vì hiệu năng kém cỏi trên hệ thống chịu tải cao. Thay thế hoàn toàn cho nó là **`ss` (Socket Statistics)** — một công cụ cực kỳ mạnh mẽ, chạy nhanh như chớp được tích hợp sẵn trong gói `iproute2`.

Bài viết này sẽ đưa bạn từ bản chất kiến trúc bên dưới Linux Kernel đến thực hành làm chủ `ss`, giải mã các chỉ số chuyên sâu (`Recv-Q`, `Send-Q`, TCP internal metrics) và cung cấp các công thức thực chiến để khắc phục sự cố mạng trên môi trường Production và Kubernetes.

---

## 1. Tại sao `ss` thay thế hoàn toàn `netstat`? (Bản chất bên dưới Linux Kernel)

Nhiều người nghĩ rằng `ss` chỉ đơn giản là một bản viết lại hiện đại hơn của `netstat`. Nhưng thực tế, **cơ chế tương tác với Linux Kernel giữa hai công cụ này hoàn toàn khác biệt**.

### Hạn chế chết người của `netstat`
- `netstat` lấy dữ liệu bằng cách đọc các file văn bản trong procfs: `/proc/net/tcp`, `/proc/net/udp`, `/proc/net/raw`...
- Khi một tiến trình đọc các file này, Kernel Linux phải:
  1. Giữ khóa **spinlock** trên bảng socket nội bộ để bảo vệ tính toàn vẹn dữ liệu.
  2. Duyệt tuần tự qua từng socket trong RAM.
  3. Định dạng (format) thông tin từng socket thành các dòng văn bản ASCII thuần túy.
- **Hậu quả:** Trên một máy chủ có 50.000 đến 100.000 kết nối TCP đồng thời (như Nginx reverse proxy, HAProxy, Redis, API Gateway), việc chạy `netstat` có thể mất từ **vài giây đến vài chục giây**. Trong suốt thời gian đó, CPU bị chiếm dụng 100% chỉ để render text, spinlock bị giữ lâu làm nghẽn luôn các gói tin mạng đang đi vào, thậm chí làm sụt giảm nghiêm trọng throughput của ứng dụng!

{{< mermaid >}}
flowchart TD
    A["1. Tiến trình netstat (User space)"] -->|"Mở file văn bản"| B["File ảo: /proc/net/tcp & /proc/net/udp"]
    B -->|"Duyệt tuần tự & Khóa Spinlock"| C[("Bảng Socket Table trong Kernel")]
    C -->|"Format hàng triệu dòng text ASCII"| B
    B -->|"Parse chuỗi text, lọc regex"| D["❌ Hiển thị kết quả:<br/><b>Chậm chạp, tốn 100% CPU, giật lag mạng</b>"]

    style A fill:#1e293b,stroke:#475569,stroke-width:2px,color:#fff
    style B fill:#334155,stroke:#64748b,stroke-width:2px,color:#fff
    style C fill:#7f1d1d,stroke:#ef4444,stroke-width:2px,color:#fff
    style D fill:#991b1b,stroke:#f87171,stroke-width:2px,color:#fff
{{< /mermaid >}}

### Sức mạnh kiến trúc của `ss`
- `ss` không đọc `/proc/net/tcp`. Nó sử dụng giao thức **Netlink API** (`sock_diag` kernel module — cụ thể là `NETLINK_INET_DIAG`).
- Thay vì chuyển đổi sang text rồi gửi cho userspace parse lại, `ss` gửi một yêu cầu nhị phân (binary request) xuống kernel.
- Kernel xử lý bộ lọc (filtering) ngay trong kernel-space và trả về các cấu trúc nhị phân (`struct inet_diag_msg`) trực tiếp qua Netlink socket buffer.
- **Kết quả:** `ss` có thể quét hàng trăm ngàn socket chỉ trong **vài phần trăm giây** (vài chục milliseconds) với mức tiêu thụ CPU và RAM gần như bằng 0.

{{< mermaid >}}
flowchart TD
    A["1. Tiến trình ss (User space)"] -->|"Gửi yêu cầu nhị phân (Binary Request)"| B["Netlink Socket API (AF_NETLINK)"]
    B -->|"Truy vấn sock_diag subsystem"| C[("Bảng Socket Table trong Kernel<br/><i>(Lọc trực tiếp trong Kernel-space)</i>")]
    C -->|"Trả về cấu trúc nhị phân struct inet_diag_msg"| D["Zero-Copy Binary Stream"]
    D -->|"Nhận dữ liệu ngay lập tức"| E["⚡ Hiển thị kết quả:<br/><b>Tức thì trong vài mili-giây, CPU & RAM ~ 0%</b>"]

    style A fill:#1e293b,stroke:#475569,stroke-width:2px,color:#fff
    style B fill:#064e3b,stroke:#10b981,stroke-width:2px,color:#fff
    style C fill:#065f46,stroke:#34d399,stroke-width:2px,color:#fff
    style E fill:#047857,stroke:#6ee7b7,stroke-width:2px,color:#fff
{{< /mermaid >}}

### Bảng so sánh chi tiết giữa `netstat` và `ss`

| Tiêu chí | 🛑 `netstat` (Lỗi thời) | ⚡ `ss` (Socket Statistics) |
| :--- | :--- | :--- |
| **Gói cài đặt** | `net-tools` (Deprecated từ 2011) | `iproute2` (Chuẩn mặc định trên mọi Linux distro hiện đại) |
| **Cơ chế thu thập** | Đọc & parse text từ `/proc/net/*` | Giao tiếp nhị phân qua **Netlink `sock_diag`** API |
| **Tốc độ thực thi** | Rất chậm khi số socket > 10.000 (tính bằng giây) | **Gần như tức thì** ngay cả khi có 100.000+ sockets (milliseconds) |
| **Ảnh hưởng hệ thống** | Chiếm CPU, giữ kernel spinlock gây lag mạng | Overhead cực thấp, an toàn tuyệt đối trên Production |
| **Bộ lọc (Filtering)** | Không có; phải pipe qua `grep`, `awk`, `sed` | **Filtering Engine tích hợp sâu**: lọc theo port, IP, CIDR, state trực tiếp |
| **Thông số nội tại** | Chỉ hiển thị thông tin cơ bản | Cung cấp **TCP internal metrics**: RTT, cwnd, retrans, MSS, TCP options... |

---

## 2. Cú pháp cơ bản & Các tùy chọn cốt lõi (Flags Cheat Sheet)

Cú pháp cơ bản của lệnh `ss`:

```bash
ss [OPTIONS] [FILTER]
```

### 1. Nhóm cờ lựa chọn giao thức (Protocol Selection)
- **`-t`** (`--tcp`): Chỉ hiển thị các socket **TCP**.
- **`-u`** (`--udp`): Chỉ hiển thị các socket **UDP**.
- **`-x`** (`--unix`): Chỉ hiển thị các **UNIX Domain Sockets** (giao tiếp IPC cục bộ, ví dụ socket của Docker, systemd, PostgreSQL local).
- **`-w`** (`--raw`): Chỉ hiển thị các socket **RAW** (dùng cho ICMP, ping, packet crafting).
- **`-4`** / **`-6`**: Chỉ lọc theo IPv4 hoặc IPv6.

### 2. Nhóm cờ trạng thái hiển thị
- **`-l`** (`--listening`): Chỉ hiển thị các socket đang ở trạng thái **LISTEN** (các port server đang mở để đợi kết nối).
- **`-a`** (`--all`): Hiển thị **tất cả** các socket (cả listening và non-listening như Established, Time-wait, Close-wait...).
- Không truyền `-l` hoặc `-a`: Mặc định `ss` chỉ hiển thị các socket **non-listening** đã kết nối thành công (`ESTABLISHED`).

### 3. Nhóm cờ định danh & tiến trình (Tối quan trọng)
- **`-n`** (`--numeric`): **KHÔNG phân giải tên miền (hostname) hoặc tên dịch vụ (service port)**.
  > ⚠️ **LƯU Ý CỰC KỲ QUAN TRỌNG:** Luôn luôn dùng `-n` khi debug trên Production! Nếu không có `-n`, `ss` sẽ cố gắng thực hiện Reverse DNS lookup cho từng địa chỉ IP và dịch port số sang tên (ví dụ: port 80 -> `http`, port 443 -> `https`). Nếu hệ thống DNS đang bị chậm hoặc sập, lệnh `ss` sẽ bị treo cứng!
- **`-p`** (`--processes`): Hiển thị tiến trình đang sở hữu socket, bao gồm **Process Name**, **PID**, và **File Descriptor (fd)**. (Cần quyền `sudo` hoặc `root` để xem tiến trình của người dùng khác).

### 4. Nhóm cờ thông số nội tại (Internal Metrics)
- **`-i`** (`--info`): Hiển thị thông số nội tại chi tiết của kết nối TCP: Round-Trip Time (RTT), Congestion Window (`cwnd`), Maximum Segment Size (`mss`), số lần retransmission...
- **`-e`** (`--extended`): Hiển thị thêm thông tin định danh: UID, Socket Inode, Socket Cookie.
- **`-m`** (`--memory`): Hiển thị bộ nhớ đệm (buffer memory) được cấp phát cho socket: `rmem` (read buffer), `wmem` (write buffer), `fmem`.
- **`-s`** (`--summary`): Xuất báo cáo tóm tắt số lượng socket theo từng giao thức mà không cần in danh sách chi tiết.

### Bộ ba câu lệnh "gối đầu giường"
```bash
# 1. Xem tất cả các cổng đang lắng nghe (Listening ports) kèm PID sở hữu
sudo ss -tulnp

# 2. Xem tất cả kết nối TCP đang hoạt động (cả Established, Time-wait, Listen...)
sudo ss -tanp

# 3. Xem tổng quan số lượng socket tức thì
ss -s
```

---

## 3. "Bí kíp" giải mã hai cột Recv-Q và Send-Q (Điểm 90% kỹ sư hiểu sai)

Khi chạy lệnh `ss`, bạn sẽ luôn nhìn thấy hai cột: **`Recv-Q`** và **`Send-Q`**. 

Rất nhiều kỹ sư mặc định rằng: `Recv-Q` là dữ liệu nhận vào, `Send-Q` là dữ liệu gửi ra. Điều này **chỉ đúng một nửa**! 

Ý nghĩa của hai cột này **hoàn toàn đảo ngược** phụ thuộc vào việc socket đó đang ở trạng thái **`LISTEN`** hay **`ESTABLISHED`**.

### Trường hợp 1: Khi Socket ở trạng thái `LISTEN` (`ss -l`)

```text
State      Recv-Q Send-Q  Local Address:Port   Peer Address:Port  Process
LISTEN     0      128           0.0.0.0:8000         0.0.0.0:*      users:(("python3",pid=1234,fd=3))
LISTEN     129    128           0.0.0.0:8080         0.0.0.0:*      users:(("node",pid=5678,fd=15))
```

Ở trạng thái `LISTEN`:
- **`Send-Q` (Send Queue Limit):** Thể hiện **kích thước tối đa của Accept Queue (Backlog)** mà kernel cho phép socket này xếp hàng. Giá trị này được xác định bởi:
  ```text
  Send-Q = min(backlog trong code ứng dụng, /proc/sys/net/core/somaxconn)
  ```
- **`Recv-Q` (Current Pending Connections):** Thể hiện **số lượng kết nối TCP đã hoàn thành bắt tay 3 bước (3-Way Handshake) nhưng ứng dụng CHƯA gọi hàm `accept()` để tiếp nhận**.

> 🚨 **DẤU HIỆU CẢNH BÁO NGUY HIỂM:**
> - Nếu **`Recv-Q = 0`**: Trạng thái lý tưởng. Ứng dụng xử lý và gọi `accept()` cực nhanh, không có kết nối nào bị ứ đọng.
> - Nếu **`Recv-Q > 0`**: Ứng dụng đang bị quá tải, thread pool/worker bận rộn nên không kịp `accept()` kết nối mới.
> - Nếu **`Recv-Q >= Send-Q`** (như port 8080 ở trên: `129/128`): **Accept Queue đã bị TRÀN (Overflow)!**
>   - Kernel sẽ bắt đầu **drop (vứt bỏ)** các gói SYN/ACK tiếp theo hoặc gửi cờ RST (tùy thuộc vào thiết lập `net.ipv4.tcp_abort_on_overflow`).
>   - Client bên ngoài sẽ gặp hiện tượng: **Connection timed out** hoặc **Connection refused** ngắt quãng dù CPU máy chủ có thể chưa chạm 100%!

{{< mermaid >}}
flowchart TD
    A["1. Client gửi ACK (Hoàn tất 3-Way Handshake)"] --> B["2. Accept Queue trong Linux Kernel"]
    B -->|"Dung lượng tối đa: Send-Q = min(backlog, somaxconn)"| C[("Giới hạn hàng đợi Accept Queue")]
    B -->|"Số kết nối đang chờ: Recv-Q"| D["3. Kết nối xếp hàng chờ Worker gọi accept()"]
    D -->|"Ứng dụng gọi accept()"| E["4. Worker nhận kết nối và phục vụ Request"]

    style A fill:#1e293b,stroke:#38bdf8,stroke-width:2px,color:#fff
    style B fill:#334155,stroke:#f59e0b,stroke-width:2px,color:#fff
    style D fill:#7f1d1d,stroke:#ef4444,stroke-width:2px,color:#fff
    style E fill:#064e3b,stroke:#10b981,stroke-width:2px,color:#fff
{{< /mermaid >}}

### Trường hợp 2: Khi Socket ở trạng thái `ESTABLISHED` (`ss -t`)

```text
State      Recv-Q Send-Q  Local Address:Port       Peer Address:Port       Process
ESTAB      0      0       192.168.1.10:8000        192.168.1.55:49210      users:(("gunicorn",pid=210,fd=7))
ESTAB      84210  0       192.168.1.10:8000        192.168.1.80:51224      users:(("gunicorn",pid=211,fd=8))
ESTAB      0      245600  192.168.1.10:5432        192.168.1.99:38112      users:(("postgres",pid=990,fd=12))
```

Ở trạng thái `ESTABLISHED`:
- **`Recv-Q` (Receive Buffer Bytes):** Số lượng **bytes dữ liệu** đã được card mạng và kernel tiếp nhận thành công, đang nằm trong Socket Receive Buffer nhưng ứng dụng **chưa đọc bằng lệnh `read()` / `recv()`**.
  - *Ý nghĩa khi `Recv-Q` cao:* Ứng dụng phía server bị nghẽn (CPU lock, garbage collection freeze, IO chậm), dữ liệu client gửi đến đang bị dồn ứ trong RAM của kernel. Nếu bộ đệm đầy, kernel sẽ gửi TCP Zero Window để bảo client dừng gửi.
- **`Send-Q` (Send Buffer Bytes):** Số lượng **bytes dữ liệu** mà ứng dụng đã gọi `write()` / `send()` để đẩy đi, nhưng **chưa nhận được gói tin xác nhận (ACK) từ phía nhận**.
  - *Ý nghĩa khi `Send-Q` cao:* Dữ liệu đã gửi đi nhưng bị kẹt lại trên đường truyền mạng (nghẽn băng thông, rớt gói tin gây retransmission liên tục) HOẶC máy nhận ở đầu bên kia bị quá tải, không kịp xử lý dữ liệu và đang set TCP Window Size về mức rất nhỏ.

{{< mermaid >}}
flowchart TD
    subgraph RecvBuffer ["📥 Chiều Nhận: Kernel Socket Receive Buffer (Recv-Q)"]
        direction TB
        R1["1. Dữ liệu từ mạng đến card mạng"] --> R2["2. Socket Receive Buffer trong Kernel<br/><i>(Dung lượng bytes chưa đọc = Recv-Q)</i>"]
        R2 -->|"Ứng dụng gọi read() / recv()"| R3["3. Tiến trình ứng dụng xử lý dữ liệu"]
    end

    RecvBuffer ~~~ SendBuffer

    subgraph SendBuffer ["📤 Chiều Gửi: Kernel Socket Send Buffer (Send-Q)"]
        direction TB
        S1["1. Ứng dụng gọi write() / send()"] --> S2["2. Socket Send Buffer trong Kernel<br/><i>(Dung lượng bytes đang chờ ACK = Send-Q)</i>"]
        S2 -->|"Đẩy gói tin ra mạng và đợi ACK"| S3["3. Gửi ra Network đến Remote Host"]
    end
{{< /mermaid >}}

### Bảng tóm tắt nhanh Recv-Q và Send-Q

| Trạng thái Socket | Cột `Recv-Q` | Cột `Send-Q` |
| :--- | :--- | :--- |
| **`LISTEN`** | Số kết nối TCP đang xếp hàng chờ `accept()` | Kích thước tối đa của hàng đợi Accept Queue |
| **`ESTABLISHED`** | Số **bytes** trong bộ đệm nhận (chưa được app đọc) | Số **bytes** trong bộ đệm gửi (chưa được bên kia ACK) |

---

## 4. Làm chủ bộ lọc chuyên sâu (Advanced Filtering Engine)

Điểm vượt trội khiến `ss` bỏ xa `netstat` chính là **bộ lọc tích hợp sẵn (Built-in Filter Engine)**. Bạn không cần phải `ss | grep | awk`, mà có thể viết các biểu thức lọc logic cực kỳ mạnh mẽ.

### 1. Lọc theo trạng thái kết nối TCP (`state`)

Cú pháp: `ss -tan state <STATE_NAME>`

```bash
# Chỉ hiển thị các kết nối đang ở trạng thái ESTABLISHED
ss -tan state established

# Chỉ hiển thị các socket đang bị kẹt ở CLOSE-WAIT (Rò rỉ socket)
ss -tan state close-wait

# Xem các kết nối ở trạng thái TIME-WAIT
ss -tan state time-wait

# Xem các kết nối đang gửi SYN nhưng chưa nhận được phản hồi (Dấu hiệu timeout/firewall drop)
ss -tan state syn-sent
```

Các tên trạng thái chuẩn được hỗ trợ:
`established`, `syn-sent`, `syn-recv`, `fin-wait-1`, `fin-wait-2`, `time-wait`, `closed`, `close-wait`, `last-ack`, `listening`, `closing`.

Ngoài ra, `ss` còn hỗ trợ các **trạng thái gom nhóm (State Groups)** cực kỳ tiện lợi:
- **`connected`**: Gom tất cả các trạng thái ngoại trừ `listening` và `closed`.
- **`synchronized`**: Tất cả các trạng thái `connected` ngoại trừ `syn-sent`.
- **`bucket`**: Các socket mini trạng thái (`time-wait` và `syn-recv`).

```bash
# Xem toàn bộ kết nối đã được thiết lập (loại trừ LISTEN)
ss -tan state connected
```

> 💡 **TÌM HIỂU SÂU VỀ CÁC TRẠNG THÁI TCP (TCP CONNECTION STATES):**
> Các trạng thái như `ESTABLISHED`, `CLOSE-WAIT`, `TIME-WAIT`, `SYN-SENT`, `FIN-WAIT-2`... có ý nghĩa và vòng đời như thế nào trong Linux? Tại sao bắt tay mở kết nối cần 3 bước nhưng đóng kết nối lại cần tới 4 bước, và vì sao socket bị kẹt ở `CLOSE-WAIT` hay bùng nổ `TIME-WAIT`?
> 
> 👉 Để nắm rõ trọn vẹn bản chất cỗ máy trạng thái và cách xử lý sự cố rò rỉ socket trên Production, mời bạn đọc bài viết chuyên sâu:  
> **[Giải mã toàn diện vòng đời và 11 trạng thái kết nối TCP (TCP Connection States) từ lý thuyết đến thực chiến](/posts/tcp-connection-states/)**.

### 2. Lọc theo Cổng (Port) và Địa chỉ IP

`ss` hỗ trợ hai từ khóa:
- **`sport`** (Source Port): Cổng nguồn.
- **`dport`** (Destination Port): Cổng đích.
- **`src`** (Source Address): Địa chỉ nguồn.
- **`dst`** (Destination Address): Địa chỉ đích.

```bash
# Lọc tất cả kết nối mà Local Port (hoặc Server Port) là 8000
ss -tan 'sport = :8000'

# Lọc tất cả kết nối mà máy chủ đang gọi tới Database PostgreSQL từ xa (port 5432)
ss -tan 'dport = :5432'

# Lọc các kết nối liên quan tới port 8000 (bất kể là local port hay remote port)
ss -tan '( sport = :8000 or dport = :8000 )'

# Lọc các kết nối kết nối tới một dải mạng con CIDR cụ thể
ss -tan 'dst 10.244.0.0/16'

# Lọc các kết nối ra ngoài cổng lớn hơn 1024
ss -tan 'dport > :1024'
```

### 3. Kết hợp điều kiện logic phức tạp

Bạn có thể dùng các toán tử boolean: `and`, `or`, `!` (not), `>`, `<`, `==`, `!=`:

```bash
# Tìm các kết nối tới cổng 443 nhưng KHÔNG PHẢI từ IP 192.168.1.1
ss -tan 'dport = :443 and ! dst 192.168.1.1'

# Tìm các socket có Recv-Q hoặc Send-Q đang bị ứ đọng trên port 8080
ss -tanp 'sport = :8080' | awk 'NR==1 || ($2 > 0 || $3 > 0)'
```

---

## 5. Bốn kịch bản sự cố Production kinh điển & Công thức chẩn đoán

### Kịch bản 1: Rò rỉ Socket `CLOSE-WAIT` (Socket Leak)

{{< mermaid >}}
sequenceDiagram
    autonumber
    actor Client as Phía Client (Muốn ngắt kết nối)
    participant Kernel as Linux Kernel (Server)
    participant App as Ứng dụng Server (App Code)

    Client->>Kernel: Gửi gói tin FIN
    Kernel->>Client: Trả lời ACK (Chuyển socket sang CLOSE-WAIT)
    Kernel->>App: Gửi tín hiệu EOF / Hup (Báo socket đã bị đóng)
    Note over App: ❌ LỖI: Code không gọi socket.close()<br/>(Do uncaught exception hoặc quên close pool)
    Note over Kernel: Socket kẹt vĩnh viễn ở CLOSE-WAIT!<br/>File Descriptor không được giải phóng!
{{< /mermaid >}}

- **Bản chất:** Khi Client gửi `FIN` để ngắt kết nối, Kernel của Server lập tức gửi `ACK` và đưa socket vào trạng thái `CLOSE-WAIT`. Lúc này, trách nhiệm của ứng dụng Server là phải gọi hàm `close()` trên socket đó. Nếu code ứng dụng bị lỗi logic, không đóng connection trong khối `finally`/`context manager`, socket sẽ nằm ở `CLOSE-WAIT` vô thời hạn.
- **Hậu quả:** Mỗi socket `CLOSE-WAIT` giữ lại một File Descriptor (FD). Khi số lượng socket này vượt quá giới hạn file mở (`ulimit -n`), toàn bộ tiến trình sẽ sụp đổ với lỗi:
  ```text
  OSError: [Errno 24] Too many open files
  ```
- **Lệnh điều tra ngay:**
  ```bash
  # Đếm số lượng kết nối CLOSE-WAIT
  ss -tan state close-wait | wc -l

  # Tìm chính xác PID và Process đang làm rò rỉ socket
  sudo ss -tanp state close-wait
  ```
- **Cách khắc phục:** Dựa vào PID tìm được, kiểm tra log ứng dụng, audit lại các khối kết nối HTTP client, Database client, Redis client để đảm bảo có cơ chế timeout và đóng connection trong khối `finally`.

---

### Kịch bản 2: Bão `TIME-WAIT` (High Connection Churn)

- **Bản chất:** Phía nào **chủ động gọi đóng kết nối trước (gửi FIN đầu tiên)** sẽ phải trải qua trạng thái `TIME-WAIT`. Trên Linux, thời gian sống của socket ở trạng thái này cố định là **`2 × MSL`** (Maximum Segment Lifetime), tương đương **60 giây**.
- **Nguyên nhân phổ biến:**
  - Microservice gọi REST API hoặc Database mà **không sử dụng HTTP Keep-Alive** hoặc **không dùng Connection Pool**.
  - Mỗi request mới sinh ra một socket TCP mới, gửi xong thì đóng ngay (Connection: close).
- **Hậu quả:**
  - Khi có 1.000 request/giây, trong 60 giây sẽ có 60.000 socket ở trạng thái `TIME-WAIT`.
  - Toàn bộ dải ephemeral ports nội bộ (mặc định khoảng 28.232 ports từ 32768 đến 60999 trong `net.ipv4.ip_local_port_range`) bị sử dụng hết!
  - Ứng dụng không thể tạo thêm kết nối ra ngoài, báo lỗi:
    ```text
    Failed to connect: Cannot assign requested address (EADDRNOTAVAIL)
    ```
- **Lệnh điều tra ngay:**
  ```bash
  # Xem thống kê số lượng TIME-WAIT tổng quan
  ss -s

  # Thống kê Top IP đích đang tích tụ nhiều TIME-WAIT nhất
  ss -tan state time-wait | awk '{print $4}' | sed -E 's/:[0-9]+$//' | sort | uniq -c | sort -nr | head -n 10
  ```
- **Cách khắc phục:**
  1. **Bắt buộc bật Keep-Alive và Connection Pooling** ở tầng ứng dụng (dùng chung kết nối cho nhiều request).
  2. Tinh chỉnh kernel cho phép tái sử dụng socket `TIME-WAIT` an toàn cho kết nối đi ra:
     ```bash
     # Kích hoạt tcp_tw_reuse trong /etc/sysctl.conf
     echo "net.ipv4.tcp_tw_reuse = 1" >> /etc/sysctl.conf
     sysctl -p
     ```
  3. Mở rộng dải port cục bộ nếu cần:
     ```bash
     sysctl -w net.ipv4.ip_local_port_range="10240 65535"
     ```

---

### Kịch bản 3: Nghẽn Accept Queue (Server sụt giảm Throughput)

- **Bản chất:** Client ồ ạt gửi request tới server. Quá trình bắt tay 3 bước hoàn thành, kết nối nằm trong Accept Queue chờ ứng dụng gọi `accept()`. Nhưng ứng dụng bị nghẽn (Worker threads bị chiếm dụng hết bởi các query chậm hoặc blocking I/O) nên không gọi `accept()` kịp.
- **Lệnh điều tra ngay:**
  ```bash
  sudo ss -lntp
  ```
  Quan sát hai cột `Recv-Q` và `Send-Q` tại port dịch vụ của bạn (ví dụ port 8000):
  ```text
  State   Recv-Q  Send-Q   Local Address:Port   Peer Address:Port   Process
  LISTEN  128     128            0.0.0.0:8000        0.0.0.0:*       users:(("gunicorn",pid=1024,fd=6))
  ```
  Nếu thấy `Recv-Q >= Send-Q`, queue đã đầy 100%!
- **Cách khắc phục:**
  1. Tăng giới hạn hàng đợi kết nối của Linux Kernel:
     ```bash
     sysctl -w net.core.somaxconn=4096
     ```
  2. Tăng tham số `backlog` trong cấu hình web server (Nginx, Gunicorn, Uvicorn, Tomcat):
     ```python
     # Ví dụ trong Gunicorn
     gunicorn --backlog 4096 --workers 8 app:wsgi
     ```
  3. Tối ưu code xử lý request hoặc scale thêm số lượng replicas/pod.

---

### Kịch bản 4: SYN-SENT tăng cao bất thường (Blackhole / Firewall Drop)

- **Bản chất:** Ứng dụng cố gắng kết nối ra một địa chỉ bên ngoài (gửi gói `SYN`) nhưng socket bị kẹt ở trạng thái `SYN-SENT` mãi không chuyển sang `ESTABLISHED`.
- **Lệnh điều tra ngay:**
  ```bash
  ss -tan state syn-sent
  ```
- **Nguyên nhân:**
  - **Firewall / Security Group / NetworkPolicy** đang âm thầm DROP (chặn) gói tin mà không phản hồi gói RST.
  - DNS phân giải ra địa chỉ IP không tồn tại hoặc destination service đã bị chết (hung/down).
  - Định tuyến mạng (Routing / Gateway) bị sai cấu hình.

---

## 6. Điều tra hiệu năng gói tin chuyên sâu với `ss -i` (Internal TCP Metrics)

Khi hệ thống không bị lỗi crash nhưng người dùng phàn nàn rằng "mạng bị lag" hoặc "request chập chờn", cờ **`-i`** của `ss` chính là cứu tinh. Nó trích xuất trực tiếp các chỉ số điều khiển luồng (TCP Congestion Control) từ kernel:

```bash
ss -ti 'dport = :443'
```

Output thực tế:
```text
ESTAB      0      0       192.168.1.10:48210     104.21.44.20:443
	 cubic wscale:7,7 rto:204 rtt:4.21/0.85 ato:40 mss:1440 rcvspace:14600
	 ssthresh:16 cwnd:10 bytes_acked:145210 retrans:0/2
```

### Các chỉ số "vàng" cần quan tâm:
1. **`rtt:4.21/0.85` (Round Trip Time):** Thời gian gói tin đi từ server tới client và nhận lại ACK (tính bằng mili-giây) cùng độ lệch chuẩn (variance).
   - Giúp bạn biết chính xác độ trễ mạng thực tế của từng client mà không cần chạy `ping`.
2. **`cwnd:10` (Congestion Window):** Kích thước cửa sổ tắc nghẽn (tính theo số gói tin MSS).
   - Nếu mạng thông thoáng, `cwnd` sẽ tăng dần theo thuật toán (CUBIC/BBR). Nếu `cwnd` tụt về 1 hoặc 2, nghĩa là vừa xảy ra nghẽn mạng nghiêm trọng.
3. **`retrans:0/2` (Retransmissions):** Thể hiện số lượng gói tin bị mất và phải gửi lại.
   - `0/2` nghĩa là hiện tại không có packet nào đang retransmit, nhưng trong phiên này đã có 2 lần phải truyền lại. Đây là dấu hiệu trực tiếp của **Packet Loss** trên đường truyền!
4. **`rto:204` (Retransmission Timeout):** Thời gian (ms) kernel sẽ đợi trước khi coi như gói tin bị mất và gửi lại.

---

## 7. Tự động hóa giám sát mạng trong Kubernetes & Container

Trong các container ứng dụng (đặc biệt là distroless hoặc alpine/debian-slim tối giản), hầu hết các công cụ mạng như `ss`, `ip`, `curl`, `netstat` đều **bị loại bỏ** để giảm dung lượng và tăng bảo mật.

Để khắc phục điều này, bạn có 2 giải pháp chuẩn Production:
1. Sử dụng tính năng **`kubectl debug`** để đính kèm một Ephemeral Container có sẵn công cụ vào cùng Pod.
2. Triển khai một **Profiling Sidecar Container** chuyên dụng có chia sẻ Process & Network Namespace (`shareProcessNamespace: true`).

### Script tiện ích tự động hóa: `analyze-connections.sh`

Dưới đây là một script mẫu hoàn chỉnh, tận dụng toàn bộ sức mạnh của `ss` kết hợp cơ chế tự động chẩn đoán thông minh, sẵn sàng nhúng vào container debug:

```bash
#!/usr/bin/env bash
# ==============================================================================
# analyze-connections.sh - Tiện ích tự động chẩn đoán kết nối mạng bằng ss
# ==============================================================================
set -euo pipefail

# Kiểm tra công cụ ss
if ! command -v ss &>/dev/null; then
    echo "[LỖI] Không tìm thấy lệnh 'ss'. Vui lòng cài đặt iproute2." >&2
    exit 1
fi

format_table() {
    if command -v column &>/dev/null; then
        column -t
    else
        cat
    fi
}

echo "======================================================================"
echo "         BÁO CÁO PHÂN TÍCH KẾT NỐI MẠNG (SS NETWORK INSPECTOR)       "
echo "  Thời gian: $(date '+%Y-%m-%d %H:%M:%S')                             "
echo "======================================================================"
echo ""

# 1. Báo cáo tổng quan Socket
echo "1. 📊 TỔNG QUAN SOCKET (Summary):"
ss -s | sed 's/^/   /'
echo ""

# 2. Thống kê phân bổ trạng thái TCP
echo "2. 📈 PHÂN BỔ TRẠNG THÁI TCP:"
RAW_TCP=$(ss -tan 2>/dev/null | awk 'NR>1 {print $1}')
COUNT_ESTAB=$(echo "$RAW_TCP" | grep -c '^ESTAB' || true)
COUNT_TIMEWAIT=$(echo "$RAW_TCP" | grep -c '^TIME-WAIT' || true)
COUNT_CLOSEWAIT=$(echo "$RAW_TCP" | grep -c '^CLOSE-WAIT' || true)
COUNT_LISTEN=$(echo "$RAW_TCP" | grep -c '^LISTEN' || true)
COUNT_SYNSENT=$(echo "$RAW_TCP" | grep -c '^SYN-SENT' || true)

printf "   %-15s : %d\n" "ESTABLISHED" "$COUNT_ESTAB"
printf "   %-15s : %d\n" "LISTEN" "$COUNT_LISTEN"
printf "   %-15s : %d\n" "TIME-WAIT" "$COUNT_TIMEWAIT"
printf "   %-15s : %d\n" "CLOSE-WAIT" "$COUNT_CLOSEWAIT"
printf "   %-15s : %d\n" "SYN-SENT" "$COUNT_SYNSENT"
echo ""

# 3. Tự động phát hiện bất thường & Cảnh báo
if [[ "$COUNT_CLOSEWAIT" -gt 15 ]]; then
    echo -e "   ⚠️ [CẢNH BÁO] Số lượng kết nối CLOSE-WAIT cao ($COUNT_CLOSEWAIT)!"
    echo "      ➔ Nguy cơ rò rỉ File Descriptors (Socket Leak). Kiểm tra lại code đóng connection."
fi

if [[ "$COUNT_TIMEWAIT" -gt 500 ]]; then
    echo -e "   ⚠️ [CẢNH BÁO] Số lượng kết nối TIME-WAIT cao ($COUNT_TIMEWAIT)!"
    echo "      ➔ Tần suất tạo mới kết nối quá nhanh. Cần bật HTTP Keep-Alive / Connection Pool."
fi

if [[ "$COUNT_SYNSENT" -gt 10 ]]; then
    echo -e "   ⚠️ [CẢNH BÁO] Phát hiện nhiều kết nối bị treo ở SYN-SENT ($COUNT_SYNSENT)!"
    echo "      ➔ Kiểm tra Firewall, Security Group, DNS hoặc dịch vụ đích bị down."
fi
echo ""

# 4. Kiểm tra các socket bị ứ đọng hàng đợi (Congestion)
echo "3. ⏳ HÀNG ĐỢI SOCKET Ứ ĐỌNG (Recv-Q & Send-Q Congestion):"
QUEUE_SOCKETS=$(ss -tanp 2>/dev/null | awk '
    NR>1 {
        if (($1 == "LISTEN" && $2 > 0) || ($1 != "LISTEN" && ($2 > 0 || $3 > 0))) {
            print $0
        }
    }' || true)

if [[ -z "$QUEUE_SOCKETS" ]]; then
    echo "   ✔ Tuyệt vời! Không có socket nào bị nghẽn buffer hay đầy accept queue."
else
    echo "   ⚠️ Phát hiện các socket bị ứ đọng:"
    echo "$QUEUE_SOCKETS" | format_table | sed 's/^/   /'
fi
echo ""

# 5. Các cổng đang lắng nghe
echo "4. 🎧 CÁC CỔNG ĐANG LẮNG NGHE (Listening Ports):"
ss -tulnp 2>/dev/null | format_table | sed 's/^/   /'
echo ""
```

---

## 8. Bảng tổng hợp các lệnh `ss` thường dùng nhất (Quick Cheatsheet)

| Lệnh | Mục đích sử dụng |
| :--- | :--- |
| `ss -tulnp` | Xem tất cả các cổng **TCP & UDP đang Listen**, hiển thị dạng số kèm PID/Process. |
| `ss -tanp` | Xem tất cả các socket **TCP ở mọi trạng thái** kèm thông tin tiến trình sở hữu. |
| `ss -s` | Xem **thống kê tổng quan** tức thì (số lượng TCP estab, timewait, closed, UDP...). |
| `ss -tan state close-wait` | Tìm các kết nối bị kẹt ở **`CLOSE-WAIT`** để phát hiện rò rỉ socket (leak FD). |
| `ss -tan state time-wait` | Liệt kê các kết nối đang ở **`TIME-WAIT`** (chuẩn đoán bão kết nối churn). |
| `ss -tan 'sport = :8000'` | Lọc tất cả các kết nối đến/đi từ **cổng 8000**. |
| `ss -tan 'dport = :5432'` | Kiểm tra kết nối từ ứng dụng ra **Database PostgreSQL**. |
| `ss -lntp` | Kiểm tra **`Recv-Q` và `Send-Q`** của các server socket (phát hiện tràn Accept Queue). |
| `ss -ti 'dport = :443'` | Xem **chỉ số nội tại TCP (RTT, cwnd, retransmissions)** để đo độ trễ mạng và mất gói. |
| `ss -tan 'dst 10.0.0.0/8'` | Lọc các kết nối tới một **dải mạng con nội bộ (CIDR)**. |

---

## 9. Lời kết

Trong hộp công cụ của một kỹ sư Linux & DevOps, **`ss` không chỉ là người thay thế đơn thuần cho `netstat`, mà là một "kính hiển vi" mạng cực kỳ sắc bén**. 

Hiểu rõ cơ chế Netlink API, giải mã chính xác hai cột `Recv-Q`/`Send-Q` và thành thạo bộ lọc chuyên sâu sẽ giúp bạn rút ngắn thời gian xử lý sự cố từ hàng giờ phán đoán mù mờ xuống còn **vài giây chẩn đoán chính xác**.

Chúc bạn làm chủ hạ tầng mạng Linux vững vàng và tự tin xử lý mọi tình huống nghẽn mạng trên hệ thống Production!
