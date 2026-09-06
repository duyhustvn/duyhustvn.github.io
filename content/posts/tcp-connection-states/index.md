---
title: "Giải mã toàn diện vòng đời và 11 trạng thái kết nối TCP (TCP Connection States) từ lý thuyết đến thực nghiệm"
date: 2026-09-05T14:30:00+07:00
draft: false
description: "Hướng dẫn chi tiết, dễ hiểu về toàn bộ 11 trạng thái kết nối TCP trong Linux kèm chương trình thực nghiệm Python và lệnh ss: Bắt tay 3 bước, quá trình đóng 4 bước, cơ chế chuyên sâu của TIME-WAIT, CLOSE-WAIT, FIN-WAIT, SYN-SENT và giải mã bí mật Recv-Q/Send-Q."
summary: "Nắm trọn vẹn cỗ máy trạng thái TCP (TCP State Machine) qua lăng kính thực nghiệm Python & lệnh ss: Bắt tay 3 bước, 4 bước đóng kết nối, phân biệt Bên chủ động vs Bên bị động, giải mã kẹt CLOSE-WAIT hay bùng nổ TIME-WAIT kèm cẩm nang chẩn đoán Production."
tags: ["TCP/IP", "Networking", "Linux", "DevOps", "Troubleshooting", "System-Design", "SRE", "Performance", "Python", "Hands-on"]
categories: ["Networking & DevOps", "Linux", "Deep Dive"]
showTableOfContents: true
---

Khi giám sát hoặc điều tra sự cố mạng trên Linux (chẳng hạn thông qua các lệnh như `ss -tan` hoặc `netstat`), chúng ta thường bắt gặp cột **`State`** với một loạt các trạng thái kết nối TCP như:

```text
ESTAB      0      0       10.0.0.1:8000       10.0.0.2:45212
CLOSE-WAIT 0      0       10.0.0.1:8000       10.0.0.2:48110
TIME-WAIT  0      0       10.0.0.1:5432       10.0.0.5:52118
SYN-SENT   0      1       10.0.0.1:49200      172.217.16.4:443
FIN-WAIT-2 0      0       10.0.0.1:8000       10.0.0.8:53210
```

Rất nhiều kỹ sư cảm thấy bối rối:
- Tại sao lại có những kết nối kẹt ở **`CLOSE-WAIT`** hàng giờ liền mà không mất đi?
- Tại sao **`TIME-WAIT`** lại tồn tại tới 60 giây sau khi đóng kết nối, và nó sinh ra để làm gì?
- Tại sao khi client gọi `close()` thì phía server lại chuyển sang `CLOSE-WAIT` chứ không phải `TIME-WAIT`?
- Bên nào (Client hay Server) là người quyết định việc socket rơi vào `TIME-WAIT`?
- Các chỉ số **`Recv-Q`** và **`Send-Q`** trong lệnh `ss` thực sự mang ý nghĩa gì khi ở `LISTEN` so với `ESTABLISHED`?

Bài viết này sẽ giúp bạn **giải mã trọn vẹn Cỗ máy trạng thái TCP (TCP Finite State Machine)** theo chuẩn RFC 793, từ nguyên lý lý thuyết đến **các kịch bản kiểm chứng thực nghiệm trực tiếp bằng Python và lệnh `ss` trên Linux**.

> 🔬 **Góc thực nghiệm (TCP State Lab):**
> Bài viết đi kèm bộ mã nguồn Python thực nghiệm độc lập (không cần quyền `root`/`sudo`). Bạn có thể vừa đọc bài vừa tự tay chạy các kịch bản kiểm chứng trên máy tính của mình:
> - Tải file mã nguồn: [`labs/tcp_state_lab.py`](https://github.com/duyhustvn/duyhustvn.github.io/blob/master/labs/tcp_state_lab.py)
> - Chạy kiểm chứng toàn bộ: `python3 labs/tcp_state_lab.py --all`
> - Chạy menu tương tác: `python3 labs/tcp_state_lab.py`
> - Chế độ tạm dừng từng bước để tự mở terminal khác gõ lệnh: `python3 labs/tcp_state_lab.py --lab 3 -p`

---

## 1. Bức tranh tổng quan: 11 Trạng thái kết nối TCP

Giao thức **TCP (Transmission Control Protocol)** là một giao thức **hướng kết nối (connection-oriented)** và **đảm bảo độ tin cậy (reliable)**. Để đảm bảo dữ liệu được truyền đi đầy đủ, đúng thứ tự và không bị trùng lặp trên một môi trường mạng vốn đầy rủi ro (unreliable IP network), cả hai đầu kết nối (Client và Server) phải duy trì một **Cỗ máy trạng thái (State Machine)**.

Chuẩn mạng RFC 793 định nghĩa chính xác **11 trạng thái** của một socket TCP:

| STT | Trạng thái TCP | Ý nghĩa & Vai trò |
| :---: | :--- | :--- |
| 1 | **`CLOSED`** | Trạng thái hư cấu ban đầu và kết thúc: Không có kết nối nào tồn tại. |
| 2 | **`LISTEN`** | Server đang mở port và lắng nghe các yêu cầu kết nối từ Client. |
| 3 | **`SYN-SENT`** | Phía Client đã gửi gói tin `SYN` khởi tạo kết nối và đang đợi phản hồi `SYN-ACK`. |
| 4 | **`SYN-RECEIVED`** (`SYN-RECV`) | Phía Server đã nhận `SYN`, gửi lại `SYN-ACK` và đang đợi `ACK` cuối cùng từ Client. |
| 5 | **`ESTABLISHED`** | Kết nối hai chiều đã được thiết lập thành công! Dữ liệu có thể truyền nhận tự do. |
| 6 | **`FIN-WAIT-1`** | Phía chủ động đóng (Active Closer) gửi gói `FIN` đầu tiên và chờ đối phương phản hồi `ACK`. |
| 7 | **`FIN-WAIT-2`** | Phía chủ động đóng đã nhận được `ACK` cho gói `FIN` của mình, đang chờ đối phương gửi tiếp `FIN`. |
| 8 | **`CLOSE-WAIT`** | Phía bị động đóng (Passive Closer) nhận `FIN` từ đối tác, kernel trả lời `ACK`, chờ ứng dụng gọi `close()`. |
| 9 | **`CLOSING`** | Cả hai bên cùng gửi `FIN` gần như đồng thời (Simultaneous Close). Khá hiếm gặp. |
| 10 | **`LAST-ACK`** | Phía bị động đóng đã hoàn tất công việc, gửi `FIN` của mình đi và chờ `ACK` cuối cùng để đóng hẳn. |
| 11 | **`TIME-WAIT`** | Phía chủ động đóng gửi `ACK` cuối cùng và chờ trong khoảng thời gian `2 × MSL` (60s) trước khi giải phóng socket. |

---

## 2. Giai đoạn 1: Bắt tay 3 bước (TCP 3-Way Handshake - Khởi tạo)

Trước khi bất kỳ byte dữ liệu nào được truyền đi, Client và Server phải thực hiện nghi thức **bắt tay 3 bước** để thống nhất các thông số khởi tạo: số thứ tự tuần tự ban đầu (Initial Sequence Number - ISN), kích thước Maximum Segment Size (MSS) và các tùy chọn TCP (Window Scaling, SACK...).

{{< mermaid >}}
sequenceDiagram
    autonumber
    actor Client as Client (Ứng dụng kết nối)
    actor Server as Server (Ứng dụng lắng nghe)

    Note over Server: Trạng thái: LISTEN (ss -lntp)
    Note over Client: Trạng thái: CLOSED

    Client->>Server: Gói tin SYN (seq = x)<br/><i>"Tôi muốn kết nối với bạn, số thứ tự bắt đầu là x"</i>
    Note over Client: Chuyển sang: SYN-SENT
    Note over Server: Nhận SYN, đưa vào SYN Queue<br/>Chuyển sang: SYN-RECEIVED

    Server->>Client: Gói tin SYN-ACK (seq = y, ack = x + 1)<br/><i>"Tôi đồng ý! Số thứ tự của tôi là y, xác nhận đã nhận x"</i>
    Note over Client: Nhận SYN-ACK, hoàn tất chiều gửi<br/>Chuyển sang: ESTABLISHED

    Client->>Server: Gói tin ACK (seq = x + 1, ack = y + 1)<br/><i>"Đã nhận thông tin của bạn! Bắt đầu truyền dữ liệu"</i>
    Note over Server: Nhận ACK, đưa vào Accept Queue<br/>Chuyển sang: ESTABLISHED
{{< /mermaid >}}

### Chi tiết các bước chuyển dịch trạng thái:
1. **Server ở trạng thái `LISTEN`:** Server gọi hàm `socket()`, `bind()`, `listen()` để mở cổng đón kết nối.
2. **Client gửi `SYN` → Chuyển sang `SYN-SENT`:** Client gọi hàm `connect()`, kernel tạo một socket cục bộ, cấp phát một ephemeral port và gửi gói tin TCP mang cờ `SYN`. Lúc này socket client nằm ở trạng thái `SYN-SENT`.
3. **Server nhận `SYN` → Chuyển sang `SYN-RECEIVED`:** Kernel của server tiếp nhận gói `SYN`, cấp phát một cấu trúc kết nối tạm thời trong **SYN Queue** (Incomplete Connection Queue), gửi lại gói tin chứa cả hai cờ `SYN` và `ACK`. Socket tạm thời ở trạng thái `SYN-RECEIVED`.
4. **Client nhận `SYN-ACK` → Chuyển sang `ESTABLISHED`:** Client nhận được phản hồi, ghi nhận ISN của Server, chuyển trạng thái socket sang `ESTABLISHED` và phản hồi lại gói `ACK` xác nhận.
5. **Server nhận `ACK` → Chuyển sang `ESTABLISHED`:** Server nhận gói `ACK` cuối cùng, chuyển kết nối từ SYN Queue sang **Accept Queue** (Complete Connection Queue), socket chuyển sang trạng thái `ESTABLISHED`. Khi ứng dụng server gọi hàm `accept()`, kết nối này sẽ được trao cho một File Descriptor mới để ứng dụng bắt đầu đọc/ghi dữ liệu.

### 🧪 Thực nghiệm 1: Bắt tay 3 bước & Bí mật Recv-Q / Send-Q của LISTEN socket

Rất nhiều tài liệu mạng giải thích rằng `Recv-Q` và `Send-Q` đại diện cho số byte dữ liệu trong bộ đệm. **Điều đó chỉ đúng với kết nối đã thành lập (ESTABLISHED)!** Khi socket ở trạng thái `LISTEN`, Linux Kernel định nghĩa lại hoàn toàn hai cột này:

Hãy cùng kiểm chứng bằng kịch bản sau:
1. Server mở cổng lắng nghe với `listen(backlog=5)`.
2. 3 Client kết nối tới Server thành công.
3. **Ứng dụng Server cố tình CHƯA gọi hàm `accept()`** để đón nhận kết nối.

```python
# Trích đoạn từ labs/tcp_state_lab.py (Lab 1)
srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
srv.bind(("127.0.0.1", 0))
port = srv.getsockname()[1]
srv.listen(5)  # Backlog = 5

# 3 client gọi connect() tới, Server chưa accept()
clients = [socket.socket(socket.AF_INET, socket.SOCK_STREAM) for _ in range(3)]
for c in clients:
    c.connect(("127.0.0.1", port))
```

Chạy lệnh kiểm tra socket bằng `ss -tan`:
```bash
ss -tan 'sport = :<port> or dport = :<port>'
```

**Kết quả thực nghiệm thực tế trên Linux:**
```text
State  Recv-Q Send-Q Local Address:Port  Peer Address:Port 
LISTEN 3      5          127.0.0.1:53047      0.0.0.0:*    
ESTAB  0      0          127.0.0.1:53047    127.0.0.1:47506
ESTAB  0      0          127.0.0.1:53047    127.0.0.1:47518
ESTAB  0      0          127.0.0.1:53047    127.0.0.1:47526
```

> 💡 **Phát hiện quan trọng từ thực nghiệm:**
> 1. **`Send-Q = 5`**: Đối với socket `LISTEN`, `Send-Q` chính là kích thước hàng đợi kết nối tối đa (tham số `backlog` truyền vào hàm `listen()`).
> 2. **`Recv-Q = 3`**: Không phải byte dữ liệu! Đây là **số lượng kết nối TCP đã hoàn tất bắt tay 3 bước thành công và đang nằm trong Accept Queue (Hàng đợi chấp nhận)** chờ ứng dụng gọi hàm `accept()`.
> 3. Cả 3 client đều đã hiển thị trạng thái `ESTAB`. Điều này chứng minh quá trình bắt tay 3 bước do chính **Linux Kernel** xử lý độc lập, ứng dụng chưa cần gọi `accept()` thì kết nối TCP ở tầng mạng đã thiết lập xong!

### ⚠️ Các sự cố thường gặp trong giai đoạn bắt tay:
- **Kẹt ở `SYN-SENT`:** 
  - *Hiện tượng:* Client chạy lệnh `ss -tan state syn-sent` thấy nhiều socket tích tụ.
  - *Nguyên nhân:* Gói `SYN` gửi đi nhưng không bao giờ nhận được phản hồi. Do **Firewall/Security Group** đang âm thầm `DROP` gói tin, routing bị lỗi, hoặc IP đích không tồn tại.
- **Bùng nổ `SYN-RECEIVED` (SYN Flood Attack):**
  - *Hiện tượng:* Server có hàng chục ngàn kết nối ở trạng thái `SYN-RECV` khiến SYN Queue bị tràn, từ chối mọi kết nối mới.
  - *Nguyên nhân:* Kẻ tấn công gửi hàng loạt gói `SYN` với IP giả mạo (spoofed IP). Server gửi `SYN-ACK` nhưng không bao giờ nhận được gói `ACK` phản hồi.
  - *Giải pháp:* Kích hoạt **SYN Cookies** trong Linux kernel:
    ```bash
    sysctl -w net.ipv4.tcp_syncookies=1
    ```

---

## 3. Giai đoạn 2: Truyền nhận dữ liệu (ESTABLISHED Phase)

Khi cả hai bên đều ở trạng thái **`ESTABLISHED`**, kênh truyền thông **Full-Duplex (Hai chiều độc lập)** chính thức hoạt động:
- Mỗi bên vừa có thể gửi dữ liệu (thông qua Send Buffer), vừa có thể nhận dữ liệu (thông qua Receive Buffer).
- Mọi gói tin dữ liệu (`PSH`, `ACK`) đều mang số Sequence và Acknowledgement để đảm bảo không bị mất mát hay đảo lộn thứ tự.
- Nếu một bên bị mất gói, cơ chế TCP Retransmission (Fast Retransmit hoặc RTO Timeout) sẽ kích hoạt để gửi lại dữ liệu.

### 🧪 Thực nghiệm 2: Giai đoạn ESTABLISHED và sự biến hóa của Recv-Q / Send-Q

Khi kết nối bước vào giai đoạn `ESTABLISHED`, ý nghĩa của hai cột queue trong lệnh `ss` lập tức biến đổi:
- **`Recv-Q`**: Số byte dữ liệu đã đến kernel nhưng ứng dụng **chưa gọi `recv()` / `read()`** để lấy ra.
- **`Send-Q`**: Số byte dữ liệu ứng dụng đã gửi bằng `send()`, nhưng **chưa nhận được ACK** từ phía đối phương.

Hãy kiểm chứng: Server gửi 16,384 bytes (16KB) sang Client, nhưng Client **cố tình chưa đọc**:
```python
# Trích đoạn từ labs/tcp_state_lab.py (Lab 2)
payload = b"X" * 16384  # 16 KB dữ liệu
conn.sendall(payload)   # Server gửi dữ liệu sang
# Phía Client chưa gọi client.recv()...
```

Chạy lệnh kiểm tra bằng `ss -tan`:
```bash
ss -tan 'sport = :<port> or dport = :<port>'
```

**Kết quả thực nghiệm thực tế:**
```text
State  Recv-Q Send-Q Local Address:Port  Peer Address:Port 
LISTEN 0      1          127.0.0.1:40473      0.0.0.0:*    
ESTAB  0      0          127.0.0.1:40473    127.0.0.1:41648
ESTAB  16384  0          127.0.0.1:41648    127.0.0.1:40473
```

> 💡 **Quan sát thực tế:**
> - Tại socket phía Client (`127.0.0.1:41648`): Cột **`Recv-Q = 16384`**. Toàn bộ 16KB đang được giữ an toàn trong TCP Receive Buffer của hệ điều hành.
> - Tại socket phía Server: **`Send-Q = 0`**, chứng tỏ gói tin đã được truyền an toàn qua card mạng và phía Client đã tự động gửi gói `ACK` xác nhận cho Server.
> - Ngay khi Client gọi `client.recv(16384)`, `Recv-Q` lập tức tụt về `0`.

---

## 4. Giai đoạn 3: Đóng kết nối (TCP 4-Way Teardown) - PHẦN QUAN TRỌNG NHẤT

Đây là giai đoạn phức tạp nhất, dễ gây nhầm lẫn nhất và cũng là nguồn gốc của **95% các sự cố rò rỉ kết nối mạng trên Production**.

### Tại sao bắt tay mở kết nối cần 3 bước, nhưng đóng kết nối lại cần tới 4 bước?
> 💡 **Bản chất cốt lõi:**
> TCP là giao thức **hai chiều độc lập (Full-Duplex)**. Việc một bên muốn dừng gửi dữ liệu (gửi gói `FIN`) **chỉ có nghĩa là chiều gửi của bên đó kết thúc**. Chiều ngược lại vẫn hoàn toàn có thể tiếp tục gửi dữ liệu nếu bên kia chưa xong việc!
> 
> Do đó, mỗi chiều kết nối phải được đóng một cách độc lập:
> - Đóng chiều đi: Cần 1 gói `FIN` và 1 gói `ACK` (2 bước).
> - Đóng chiều về: Cần tiếp 1 gói `FIN` và 1 gói `ACK` (2 bước).
> ➔ Tổng cộng là **4 bước (4-Way Handshake)**.

### Khái niệm sống còn: Bên chủ động đóng (Active Closer) vs Bên bị động đóng (Passive Closer)
- **Bên chủ động đóng kết nối (Active Closer):** Bên gọi hàm `close()` trước để phát đi gói `FIN` đầu tiên. **Bên này sẽ đi qua các trạng thái: `FIN-WAIT-1` → `FIN-WAIT-2` → `TIME-WAIT`**.
- **Bên bị động đóng kết nối (Passive Closer):** Bên nhận được gói `FIN` đầu tiên từ đối tác. **Bên này sẽ đi qua các trạng thái: `CLOSE-WAIT` → `LAST-ACK`**.

> 📌 **LƯU Ý:** Bất kỳ bên nào (Client hoặc Server) đều có thể là bên chủ động đóng kết nối! Ví dụ:
> - Trong Web thông thường: Khi người dùng đóng tab trình duyệt, Client là bên chủ động đóng.
> - Nhưng trong HTTP Keep-Alive timeout: Web Server (Nginx) chủ động đóng kết nối khi hết hạn idle timeout, lúc này **Server lại chính là bên chủ động đóng!**

{{< mermaid >}}
sequenceDiagram
    autonumber
    actor Active as Bên chủ động đóng (Gọi close trước)
    actor Passive as Bên bị động đóng (Nhận FIN trước)

    Note over Active,Passive: Cả hai bên đang ở trạng thái: ESTABLISHED

    Active->>Passive: Gói tin FIN (seq = u)<br/><i>"Tôi đã gửi xong toàn bộ dữ liệu, muốn đóng chiều gửi của tôi"</i>
    Note over Active: Chuyển sang: FIN-WAIT-1
    Note over Passive: Kernel nhận FIN, chuyển sang: CLOSE-WAIT

    Passive->>Active: Gói tin ACK (ack = u + 1)<br/><i>"Đã nhận được FIN của bạn, tôi xác nhận"</i>
    Note over Active: Nhận ACK, chuyển sang: FIN-WAIT-2
    Note over Passive: Ứng dụng nhận tín hiệu EOF trên socket.<br/>Tiếp tục gửi nốt dữ liệu còn dang dở (nếu có)...

    Passive->>Active: Gói tin FIN (seq = v, ack = u + 1)<br/><i>"Tôi cũng đã xong việc, xin phép đóng chiều gửi của tôi"</i>
    Note over Passive: Chuyển sang: LAST-ACK

    Active->>Passive: Gói tin ACK (ack = v + 1)<br/><i>"Đã nhận FIN của bạn! Tạm biệt"</i>
    Note over Active: Chuyển sang: TIME-WAIT (Đếm ngược 2MSL = 60s)
    Note over Passive: Nhận ACK cuối cùng → Đóng hẳn socket (CLOSED)

    Note over Active: Hết 60s (2MSL) → Đóng hẳn socket (CLOSED)
{{< /mermaid >}}

---

## 5. Mổ xẻ chi tiết từng trạng thái đóng kết nối

### 1. `FIN-WAIT-1` (Bên chủ động đóng)
- **Xảy ra khi:** Ứng dụng gọi hàm `close()`. Kernel lập tức gửi gói tin mang cờ `FIN` sang cho đối tác và đặt socket vào trạng thái `FIN-WAIT-1`.
- **Bình thường:** Trạng thái này diễn ra cực nhanh (vài mili-giây) vì đối phương sẽ gửi lại gói `ACK` gần như ngay lập tức.
- **Khi bị kẹt:** Nếu đường truyền mạng bị đứt đúng lúc này hoặc đối phương bị sập nguồn đột ngột, gói `FIN` gửi đi sẽ không có `ACK`. Kernel sẽ tự động gửi lại gói `FIN` theo thuật toán exponential backoff dựa vào tham số `net.ipv4.tcp_orphan_retries` trước khi tự hủy socket.

### 2. `FIN-WAIT-2` (Bên chủ động đóng - Half-Closed)
- **Xảy ra khi:** Bên chủ động đóng đã nhận được `ACK` cho gói `FIN` của mình. Chiều gửi của bên chủ động đóng đã đóng hoàn toàn. Socket chuyển sang `FIN-WAIT-2` để **chờ đối phương gửi nốt gói `FIN` của họ**.
- **Khi bị kẹt:** Nếu phía bên bị động đóng bị lỗi code (treo tiến trình, không bao giờ gọi hàm `close()`), bên chủ động đóng sẽ phải chờ đợi trong vô vọng!
- **Cơ chế bảo vệ của Linux:** Để tránh việc cạn kiệt tài nguyên do đối phương "quên đóng kết nối", Linux Kernel có cơ chế timeout cưỡng chế:
  ```bash
  # Xem thời gian chờ tối đa ở FIN-WAIT-2 (mặc định: 60 giây)
  sysctl net.ipv4.tcp_fin_timeout
  ```
  Sau khoảng thời gian `tcp_fin_timeout` (mặc định 60s), nếu bên kia vẫn không gửi `FIN`, Linux sẽ **tự động tiêu hủy socket này**.

---

### 3. `CLOSE-WAIT` (Bên bị động đóng - Nguồn gốc rò rỉ Socket)

> 🚨 **ĐÂY LÀ TRẠNG THÁI NGUY HIỂM NHẤT TRÊN PRODUCTION!**

- **Xảy ra khi:** Phía bên bị động đóng nhận được gói `FIN` từ đối phương. Linux Kernel **tự động trả lời `ACK`** ngay lập tức và đưa socket vào trạng thái `CLOSE-WAIT`. Đồng thời, kernel gửi tín hiệu kết thúc file (EOF) hoặc trả về giá trị `0` / `null` khi ứng dụng gọi hàm `read()`.
- **Trách nhiệm của Lập trình viên:** 
  - Tại thời điểm này, **Kernel Linux KHÔNG THỂ tự ý đóng socket!**
  - Kernel bắt buộc phải đợi **ỨNG DỤNG** (Application code) nhận ra EOF và gọi hàm `socket.close()` (hoặc đóng stream/connection pool).
- **Tại sao socket bị KẸT ở `CLOSE-WAIT`?**
  - Nếu code ứng dụng bị lỗi ngoại lệ (uncaught exception), bị deadlock, hoặc lập trình viên quên viết lệnh đóng kết nối trong khối `finally` / `context manager`, hàm `close()` **sẽ không bao giờ được gọi**.
  - Không có bất kỳ timeout nào trong Linux Kernel có thể tự động đóng socket `CLOSE-WAIT`! Socket sẽ nằm ở `CLOSE-WAIT` **cho đến khi tiến trình đó bị KILL hoặc khởi động lại!**
- **Hậu quả:**
  - Mỗi socket `CLOSE-WAIT` chiếm 1 File Descriptor (FD) và một lượng RAM trong kernel.
  - Khi lượng socket này tích tụ vượt ngưỡng `ulimit -n` của hệ điều hành, tiến trình sẽ báo lỗi kinh điển:
    ```text
    OSError: [Errno 24] Too many open files
    ```
  - Mọi request mới đi vào server đều bị từ chối ngay lập tức, dịch vụ tê liệt hoàn toàn!

### 🧪 Thực nghiệm 3: Bản chất TCP Half-Closed (FIN-WAIT-2) và Rò rỉ Socket (CLOSE-WAIT Leak)

Một trong những câu hỏi phổ biến nhất: **Tại sao một bên đóng mà bên kia vẫn gửi được dữ liệu?** Và **tại sao `CLOSE-WAIT` lại nguy hiểm đến vậy?**

Hãy xem thực nghiệm thực tế bằng Python:
1. Client đóng chiều gửi của mình bằng `client.shutdown(socket.SHUT_WR)` (Active Closer).
2. Server nhận được EOF (`conn.recv() == b""`), kernel tự động ACK, nhưng **code ứng dụng Server cố tình KHÔNG gọi `conn.close()`**.

```python
# Trích đoạn từ labs/tcp_state_lab.py (Lab 3)
client.shutdown(socket.SHUT_WR)  # Client đóng chiều gửi (Active Closer)
time.sleep(0.1)

# Server nhận được tín hiệu EOF, nhưng CHƯA gọi conn.close()
```

Chạy lệnh kiểm tra bằng `ss -tanp`:
```bash
ss -tanp 'sport = :<port> or dport = :<port>'
```

**Kết quả thực nghiệm thực tế trên Linux:**
```text
State      Recv-Q Send-Q Local Address:Port  Peer Address:Port Process                            
LISTEN     0      5          127.0.0.1:39359      0.0.0.0:*     users:(("python3",pid=45109,fd=4))
FIN-WAIT-2 0      0          127.0.0.1:50278    127.0.0.1:39359 users:(("python3",pid=45109,fd=5))
CLOSE-WAIT 1      0          127.0.0.1:39359    127.0.0.1:50278 users:(("python3",pid=45109,fd=7))
```

> 💡 **Quan sát thực nghiệm:**
> - Phía Client chuyển sang **`FIN-WAIT-2`**: Chiều gửi đã đóng, nhưng chiều nhận vẫn mở.
> - Phía Server chuyển sang **`CLOSE-WAIT`**: Chú ý cột **`Recv-Q = 1`**! Con số `1` này ở `CLOSE-WAIT` biểu thị cờ kết thúc file (EOF) đang nằm trong receive queue chờ ứng dụng tiêu thụ.

#### Kiểm chứng tính chất Half-Closed:
Liệu Server ở `CLOSE-WAIT` có thể tiếp tục gửi dữ liệu sang Client ở `FIN-WAIT-2`?
```python
# Server gửi thêm dữ liệu khi đang ở CLOSE-WAIT:
conn.sendall(b"Server: Toi van con du lieu muon gui cho ban truoc khi dong!\n")
msg = client.recv(1024)
print(msg.decode())
# Kết quả: Client in FIN-WAIT-2 nhận thành công 100%!
```
➔ Điều này chứng minh hoàn hảo nguyên lý **TCP Half-Closed**: Chiều gửi của bên này tắt không ảnh hưởng gì tới chiều gửi của bên kia!

#### Mô phỏng rò rỉ Socket Leak trên Production:
Điều gì xảy ra nếu server có lỗi logic (exception) và bỏ quên không gọi `close()` cho các client ngắt kết nối?
Khi 4 client kết nối và ngắt, lệnh `ss -tanp state close-wait` lập tức phơi bày "thủ phạm":

```text
Recv-Q Send-Q Local Address:Port  Peer Address:Port Process                             
1      0          127.0.0.1:35463    127.0.0.1:56624 users:(("python3",pid=45725,fd=11))
1      0          127.0.0.1:35463    127.0.0.1:56622 users:(("python3",pid=45725,fd=10))
1      0          127.0.0.1:35463    127.0.0.1:56610 users:(("python3",pid=45725,fd=9)) 
1      0          127.0.0.1:35463    127.0.0.1:56598 users:(("python3",pid=45725,fd=7))
```
Mỗi kết nối `CLOSE-WAIT` đang chiếm giữ một **File Descriptor (`fd=7, 9, 10, 11`)** của hệ điều hành. Các socket này **sẽ tồn tại vĩnh viễn** cho đến khi tiến trình bị tắt, làm cạn kiệt bảng descriptor của hệ thống!

---

### 4. `LAST-ACK` (Bên bị động đóng)
- **Xảy ra khi:** Sau khi ở `CLOSE-WAIT`, ứng dụng của bên bị động đóng cuối cùng cũng gọi `close()`. Kernel gửi gói tin `FIN` sang cho bên chủ động đóng và chuyển socket sang trạng thái `LAST-ACK`.
- Socket này chỉ đơn giản là đợi gói tin `ACK` cuối cùng từ bên chủ động đóng để đóng hẳn sang `CLOSED`.
- Thường diễn ra rất nhanh, trừ khi kết nối mạng bị rớt gói tin nghiêm trọng.

---

### 5. `TIME-WAIT` (Bên chủ động đóng - Vệ sĩ của giao thức TCP)

Khi bên chủ động đóng nhận được gói `FIN` từ phía bên bị động đóng, nó gửi lại gói tin `ACK` cuối cùng và chuyển socket sang trạng thái **`TIME-WAIT`**. 

Tại sao socket không chuyển ngay sang `CLOSED` để giải phóng port luôn mà lại phải đợi **`2MSL` (Maximum Segment Lifetime = 60 giây)**?

{{< mermaid >}}
flowchart TD
    TW["Trạng thái TIME-WAIT (Kéo dài 2MSL = 60 giây)"]
    
    TW --> Reason1["🛡️ Lý do 1: Bảo vệ gói tin ACK cuối cùng"]
    Reason1 --> D1["Nếu gói ACK cuối bị rớt trên mạng:<br/>Bên bị động đóng sẽ gửi lại gói FIN.<br/>Nhờ còn ở TIME-WAIT, bên chủ động đóng sẽ gửi lại ACK<br/>giúp bên kia đóng kết nối êm đẹp!"]

    TW --> Reason2["🛡️ Lý do 2: Làm sạch các gói tin đi lạc (Ghost Packets)"]
    Reason2 --> D2["Gói tin cũ có thể bị trễ trên đường truyền.<br/>Chờ 2MSL đảm bảo mọi gói tin cũ của phiên kết nối này<br/>đều đã chết hẳn trên Internet trước khi port này<br/>được cấp phát cho một kết nối mới!"]

    style TW fill:#1e293b,stroke:#f59e0b,stroke-width:2px,color:#fff
    style Reason1 fill:#0f172a,stroke:#3b82f6,stroke-width:2px,color:#fff
    style Reason2 fill:#0f172a,stroke:#10b981,stroke-width:2px,color:#fff
{{< /mermaid >}}

#### Hai lý do sống còn của `TIME-WAIT`:
1. **Đảm bảo đóng kết nối tin cậy (Reliable Connection Termination):**
   - Giả sử bên chủ động đóng gửi gói `ACK` cuối cùng nhưng gói tin này bị rớt trên đường truyền mạng. Phía bên bị động đóng (vẫn đang ở `LAST-ACK`) không nhận được `ACK` sẽ nghĩ rằng gói `FIN` của mình bị thất lạc, nên nó sẽ **gửi lại gói `FIN`**.
   - Nếu bên chủ động đóng đóng ngay sang `CLOSED`, nó sẽ phản hồi lại gói `RST` (Connection Reset) khi nhận được gói `FIN` gửi lại kia, khiến bên bị động đóng nghĩ rằng kết nối bị lỗi đột ngột thay vì đóng êm đẹp.
   - Nhờ trạng thái `TIME-WAIT`, bên chủ động đóng giữ socket trong 60s để nếu nhận lại gói `FIN`, nó sẽ retransmit lại gói `ACK` cuối cùng.
2. **Ngăn chặn xung đột gói tin cũ (Prevent Delayed Duplicate Packets):**
   - Trên Internet, các gói tin IP có thể bị định tuyến qua đường vòng và đến muộn (delayed segments).
   - Nếu socket được đóng ngay lập tức và tái sử dụng cùng cặp `(Source IP:Port, Dest IP:Port)` cho một kết nối mới, một gói tin dữ liệu cũ bị trễ của phiên trước có thể bất ngờ xuất hiện và chèn vào kết nối mới, gây ra hiện tượng **hỏng dữ liệu (Data Corruption)**!
   - Khoảng thời gian `2MSL` đảm bảo thời gian sống tối đa của mọi gói tin trên mạng đã hết trước khi cặp địa chỉ đó được phép tái sử dụng.

#### Khi nào `TIME-WAIT` trở thành thảm họa?
- Khi một server đóng vai trò là Client (ví dụ: Microservice gọi API khác, hoặc Web App gọi Database/Redis) tạo kết nối HTTP liên tục mà **không bật Keep-Alive**.
- Mỗi request mở 1 socket, gọi xong đóng ngay → Server là bên chủ động đóng → Socket rơi vào `TIME-WAIT` trong 60 giây.
- Với 1.000 request/s, trong 60 giây sẽ có **60.000 socket `TIME-WAIT`**, nuốt trọn toàn bộ dải ephemeral ports nội bộ của máy chủ. Kết quả: lỗi `Cannot assign requested address`.

### 🧪 Thực nghiệm 4: Theo dõi bộ đếm 2MSL (60s Countdown Timer) bằng cờ ss -tan -o

Khi cả hai bên gọi `close()`, bên chủ động đóng sẽ bước vào trạng thái `TIME-WAIT`. Rất nhiều kỹ sư thắc mắc: *Làm sao biết Linux Kernel có thực sự đếm lùi 60 giây hay không?*

Lệnh `ss` với cờ **`-o` (options/timers)** sẽ hiển thị trực tiếp bộ đếm thời gian thực này:

```bash
ss -tan -o 'sport = :<port> or dport = :<port>'
```

**Kết quả quan sát tại giây thứ 1:**
```text
State     Recv-Q Send-Q Local Address:Port  Peer Address:Port 
LISTEN    0      1          127.0.0.1:35585      0.0.0.0:*    
TIME-WAIT 0      0          127.0.0.1:34644    127.0.0.1:35585 timer:(timewait,59sec,0)
```

**Kết quả quan sát sau 3 giây:**
```text
State     Recv-Q Send-Q Local Address:Port  Peer Address:Port 
LISTEN    0      1          127.0.0.1:35585      0.0.0.0:*    
TIME-WAIT 0      0          127.0.0.1:34644    127.0.0.1:35585 timer:(timewait,56sec,0)
```

> 💡 **Phát hiện:** Linux Kernel duy trì một timer riêng biệt: `timer:(timewait,56sec,0)`. Khi giá trị này chạm `0`, socket sẽ được giải phóng hoàn toàn và biến mất khỏi bảng kết nối. Phía Passive Closer (Server) đã `CLOSED` ngay lập tức và **không hề có timer nào**!

### 🧪 Thực nghiệm 5: Khi Server trở thành Active Closer (Ai sẽ chịu TIME-WAIT?)

Có một ngộ nhận kinh điển trong cộng đồng lập trình: *"Chỉ có Client mới bị TIME-WAIT, Server không bao giờ bị!"*

Thực tế: **Bên nào gọi hàm `close()` trước để phát gói `FIN` đầu tiên, bên đó sẽ là Active Closer và phải gánh chịu `TIME-WAIT`**.

Hãy xem điều gì xảy ra nếu **Server chủ động đóng trước** (ví dụ Nginx ngắt kết nối do Client hết hạn Keep-Alive idle timeout):

```python
# Trích đoạn từ labs/tcp_state_lab.py (Lab 5)
# Server chủ động đóng kết nối trước!
conn.close()
time.sleep(0.05)

# Client sau đó mới đóng socket phía mình
client.close()
```

Kiểm tra bằng `ss -tan -o`:
```text
State     Recv-Q Send-Q Local Address:Port  Peer Address:Port 
LISTEN    0      1          127.0.0.1:45163      0.0.0.0:*    
TIME-WAIT 0      0          127.0.0.1:45163    127.0.0.1:54754 timer:(timewait,59sec,0)
```

> 🚨 **Bài học thực chiến:**
> - Nhìn vào cột `Local Address:Port`: Socket `TIME-WAIT` thuộc về **cổng của Server (`127.0.0.1:45163`)**!
> - Đây là lý do tại sao các hệ thống API Gateway, Reverse Proxy (như Nginx, HAProxy) hoặc Microservices gọi sang dịch vụ khác nếu chủ động đóng kết nối liên tục thì chính **máy chủ đó sẽ bị tràn ngập socket TIME-WAIT**, dẫn đến cạn kiệt ephemeral ports hoặc file descriptors cục bộ.

---

## 6. Sơ đồ Cỗ máy trạng thái hoàn chỉnh (TCP Finite State Machine)

Dưới đây là bức tranh tổng thể kết nối tất cả các mắt xích của một phiên kết nối TCP:

{{< mermaid >}}
flowchart TD
    CLOSED["CLOSED (Trạng thái đóng ban đầu)"]
    LISTEN["LISTEN (Server mở port chờ)"]
    SYN_SENT["SYN-SENT (Client gửi SYN)"]
    SYN_RCVD["SYN-RECEIVED (Server gửi SYN-ACK)"]
    ESTAB["ESTABLISHED (Kết nối hoạt động 2 chiều)"]

    CLOSED -->|"Server: socket(), bind(), listen()"| LISTEN
    CLOSED -->|"Client: connect() [gửi SYN]"| SYN_SENT
    LISTEN -->|"Server: nhận SYN [gửi SYN-ACK]"| SYN_RCVD
    SYN_SENT -->|"Client: nhận SYN-ACK [gửi ACK]"| ESTAB
    SYN_RCVD -->|"Server: nhận ACK"| ESTAB

    ESTAB -->|"Bên chủ động đóng: gọi close() [gửi FIN]"| FIN_WAIT_1["FIN-WAIT-1"]
    FIN_WAIT_1 -->|"Nhận ACK của FIN"| FIN_WAIT_2["FIN-WAIT-2"]
    FIN_WAIT_2 -->|"Nhận FIN từ đối tác [gửi ACK]"| TIME_WAIT["TIME-WAIT (Chờ 2MSL)"]
    TIME_WAIT -->|"Hết 2MSL (60s)"| CLOSED

    ESTAB -->|"Bên bị động đóng: nhận FIN [gửi ACK]"| CLOSE_WAIT["CLOSE-WAIT (Chờ App close)"]
    CLOSE_WAIT -->|"App gọi close() [gửi FIN]"| LAST_ACK["LAST-ACK"]
    LAST_ACK -->|"Nhận ACK cuối cùng"| CLOSED

    style CLOSED fill:#334155,stroke:#64748b,color:#fff
    style LISTEN fill:#1e293b,stroke:#3b82f6,color:#fff
    style ESTAB fill:#064e3b,stroke:#10b981,stroke-width:3px,color:#fff
    style CLOSE_WAIT fill:#7f1d1d,stroke:#ef4444,stroke-width:2px,color:#fff
    style TIME_WAIT fill:#78350f,stroke:#f59e0b,stroke-width:2px,color:#fff
{{< /mermaid >}}

---

## 7. Cẩm nang chẩn đoán sự cố theo trạng thái TCP (Troubleshooting Matrix)

Khi hệ thống gặp sự cố mạng, hãy mở terminal và chạy lệnh `ss -tan` để kiểm tra phân bổ trạng thái kết nối. Bảng dưới đây là hướng dẫn xử lý chuẩn cho từng trường hợp:

| Trạng thái bất thường | Triệu chứng & Nguyên nhân | Bên nào bị ảnh hưởng? | Câu lệnh chẩn đoán bằng `ss` | Hướng xử lý triệt để |
| :--- | :--- | :--- | :--- | :--- |
| **`CLOSE-WAIT` tăng cao** | **Rò rỉ Socket (Socket Leak):** Phía client đã ngắt kết nối nhưng ứng dụng server bị uncaught exception hoặc quên gọi `close()` connection pool. | **Bên bị động đóng** (Thường là Backend / Database) | `sudo ss -tanp state close-wait` | Tìm chính xác PID của tiến trình, audit lại code đảm bảo giải phóng connection trong khối `finally`/`defer`. |
| **`TIME-WAIT` bùng nổ (> 10k)** | **Tần suất kết nối quá lớn (High Churn):** Gọi HTTP REST hoặc Database mà không dùng Keep-Alive / Connection Pool. | **Bên chủ động đóng** (Thường là Client / API Gateway) | `ss -s`<br/>`ss -tan state time-wait \| wc -l` | 1. Bật HTTP Keep-Alive & Connection Pool.<br/>2. Bật `net.ipv4.tcp_tw_reuse = 1`.<br/>3. Mở rộng dải port: `net.ipv4.ip_local_port_range`. |
| **`SYN-SENT` tăng cao** | **Mạng bị Drop / Treo:** Gửi yêu cầu kết nối nhưng không hề nhận được phản hồi (bị nuốt gói tin). | **Client** | `ss -tan state syn-sent` | Kiểm tra Firewall (iptables/nftables), Security Group, NetworkPolicy trên K8s hoặc kiểm tra DNS phân giải sai IP. |
| **`SYN-RECV` tăng cao** | **Tấn công từ chối dịch vụ (SYN Flood):** Hàng đợi SYN Queue bị tràn do nhận nhiều SYN giả mạo. | **Server** | `ss -tan state syn-recv` | Kích hoạt SYN Cookies: `net.ipv4.tcp_syncookies = 1` và tăng kích thước `tcp_max_syn_backlog`. |
| **`FIN-WAIT-1` kẹt lâu** | Phía bên kia bị đứt kết nối mạng hoặc sập nguồn đột ngột, gói `FIN` gửi đi bị mất. | **Bên chủ động đóng** | `ss -tan state fin-wait-1` | Tinh chỉnh số lần retry gói mồ côi: `sysctl -w net.ipv4.tcp_orphan_retries=2`. |
| **`FIN-WAIT-2` kẹt nhiều** | Đối phương nhận được FIN của ta nhưng phía họ bị treo, không bao giờ gửi lại FIN. | **Bên chủ động đóng** | `ss -tan state fin-wait-2` | Kiểm tra tham số timeout: `sysctl -w net.ipv4.tcp_fin_timeout=30`. |

### 🧪 Thực nghiệm 6: Mô phỏng kẹt SYN-SENT khi gói tin bị DROP (Firewall Blackhole)

Khi gặp lỗi kết nối mạng, làm sao để phân biệt giữa:
1. **Port đang bị đóng (Service Down):** Phía đích sẽ phản hồi ngay lập tức gói tin TCP `RST` (Reset). Hàm `connect()` ném lỗi `ConnectionRefusedError` chỉ trong vài mili-giây, socket **hoàn toàn không bị kẹt ở `SYN-SENT`**.
2. **Firewall âm thầm DROP gói tin (Blackhole):** Gói `SYN` gửi đi và biến mất vào hư vô. Không có gói `RST` hay `SYN-ACK` nào trả về!

Hãy kiểm chứng bằng cách kết nối non-blocking tới địa chỉ RFC 5737 TEST-NET (`192.0.2.1:80`) — dải IP chuẩn không bao giờ phản hồi gói tin:

```python
# Trích đoạn từ labs/tcp_state_lab.py (Lab 6)
s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
s.setblocking(False)
try:
    s.connect(("192.0.2.1", 80))
except BlockingIOError:
    pass  # Kết nối đang chờ xử lý ngầm
```

Kiểm tra ngay bằng lệnh `ss -tan`:
```bash
ss -tan dst 192.0.2.1
```

**Kết quả thực nghiệm thực tế:**
```text
State    Recv-Q Send-Q Local Address:Port  Peer Address:Port
SYN-SENT 0      1       192.168.1.35:55566    192.0.2.1:80
```

> 💡 **Bóc tách cốt lõi:**
> - Socket rơi vào trạng thái **`SYN-SENT`**.
> - Cột **`Send-Q = 1`**: Đại diện cho 1 gói tin `SYN` khởi tạo đang nằm trong hàng đợi gửi đi chờ xác nhận.
> - Kernel sẽ tự động gửi lại gói `SYN` nhiều lần theo thuật toán lũy thừa (Exponential Backoff: 1s, 2s, 4s, 8s...) dựa theo tham số `net.ipv4.tcp_syn_retries` trước khi chịu từ bỏ và trả về lỗi `ETIMEDOUT` (Connection timed out) sau khoảng 60–120 giây.

---

## 8. Cẩm nang câu lệnh `ss` thực chiến (Socket Statistics Cheat Sheet)

Lệnh `ss` là công cụ thay thế hiện đại, mạnh mẽ và nhanh hơn rất nhiều so với `netstat` vì nó truy vấn thông tin trực tiếp từ Kernel qua giao tiếp Netlink (`sock_diag`). Dưới đây là các cú pháp bạn sẽ dùng hàng ngày:

| Mục đích điều tra | Câu lệnh `ss` chuẩn | Giải thích cờ & cú pháp |
| :--- | :--- | :--- |
| **Xem tổng quan hệ thống** | `ss -s` | Thống kê số lượng socket tổng thể (TCP, UDP, RAW, TIME-WAIT...). |
| **Xem tất cả kết nối TCP** | `ss -tan` | `-t` (TCP), `-a` (tất cả LISTEN + Non-LISTEN), `-n` (hiển thị số port/IP). |
| **Xem kèm tiến trình & FD** | `sudo ss -tanp` | `-p` (process): Hiển thị tên tiến trình, PID và số File Descriptor (`fd=...`). |
| **Xem đếm ngược timer** | `ss -tan -o` | `-o` (options/timers): Hiển thị bộ đếm `TIME-WAIT`, keepalive countdown. |
| **Lọc theo trạng thái cụ thể** | `ss -tan state established`<br/>`ss -tan state close-wait`<br/>`ss -tan state time-wait` | Sử dụng từ khóa `state <tên_trạng_thái>` viết thường. |
| **Lọc theo cổng cục bộ** | `ss -tan 'sport = :8080'` | Lọc cổng nguồn (Source Port). |
| **Lọc theo cổng đích** | `ss -tan 'dport = :443'` | Lọc cổng đích (Destination Port). |
| **Lọc theo IP đích** | `ss -tan dst 192.168.1.1` | Tìm toàn bộ socket đang hướng tới một máy chủ cụ thể. |

---

## 9. Lời kết

Hiểu rõ 11 trạng thái kết nối TCP cùng bản chất cỗ máy trạng thái FSM qua lăng kính **thực nghiệm** là một trong những kỹ năng nền tảng quan trọng nhất phân biệt giữa một kỹ sư chỉ biết "khởi động lại service khi gặp lỗi" và một kỹ sư có khả năng "chẩn đoán và khắc phục sự cố mạng tận gốc rễ".

Ghi nhớ 4 bài học thực nghiệm cốt lõi:
1. **`Recv-Q` và `Send-Q` có 2 bộ mặt:** Ở trạng thái `LISTEN`, chúng là **Accept Queue** và **Backlog size**; ở trạng thái `ESTABLISHED`, chúng mới là **Byte dữ liệu chưa đọc / chưa ACK**.
2. **`CLOSE-WAIT` là lỗi của tầng ứng dụng** (không gọi `close()` hoặc deadlock). Kernel Linux **không bao giờ tự động dọn dẹp** socket `CLOSE-WAIT`, dẫn tới cạn kiệt File Descriptor (`Too many open files`).
3. **`TIME-WAIT` là cơ chế bảo vệ của TCP**, và nó xuất hiện ở **bất kỳ bên nào gọi `close()` trước** (kể cả Server). Muốn giảm bớt trên Server, bắt buộc phải bật Connection Pooling và HTTP Keep-Alive.
4. **`SYN-SENT` tích tụ là dấu hiệu của mạng bị DROP gói tin** (Firewall / Security Group / Sai route), hoàn toàn khác với việc Port bị đóng (nhận ngay gói `RST`).

Toàn bộ mã nguồn thực nghiệm trong bài viết đã được đóng gói sẵn trong script:  
👉 [`labs/tcp_state_lab.py`](https://github.com/duyhustvn/duyhustvn.github.io/blob/master/labs/tcp_state_lab.py)

Chúc bạn tự tin làm chủ và làm chủ hoàn toàn mọi trạng thái socket trong hệ thống Linux của mình!
