---
title: "Giải mã toàn diện vòng đời và 11 trạng thái kết nối TCP (TCP Connection States) từ lý thuyết đến thực chiến"
date: 2026-09-05T14:30:00+07:00
draft: false
description: "Hướng dẫn chi tiết, dễ hiểu về toàn bộ 11 trạng thái kết nối TCP trong Linux: Bắt tay 3 bước (3-Way Handshake), quá trình đóng kết nối (4-Way Teardown), cơ chế chuyên sâu của TIME-WAIT, CLOSE-WAIT, FIN-WAIT, SYN-SENT và cách khắc phục lỗi rò rỉ socket trên Production."
summary: "Nắm trọn vẹn cỗ máy trạng thái TCP (TCP State Machine): Bản chất bắt tay 3 bước và 4 bước đóng kết nối, phân biệt vai trò Bên chủ động vs Bên bị động đóng kết nối, giải mã tại sao kẹt CLOSE-WAIT hay bùng nổ TIME-WAIT kèm cẩm nang chẩn đoán Production."
tags: ["TCP/IP", "Networking", "Linux", "DevOps", "Troubleshooting", "System-Design", "SRE", "Performance"]
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

Bài viết này sẽ giúp bạn **giải mã trọn vẹn Cỗ máy trạng thái TCP (TCP Finite State Machine)** theo chuẩn RFC 793, từ nguyên lý bắt tay 3 bước, 4 bước đóng kết nối đến các tình huống thực chiến khắc phục sự cố mạng trên môi trường Production.

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

---

## 8. Lời kết

Hiểu rõ 11 trạng thái kết nối TCP cùng bản chất cỗ máy trạng thái FSM là một trong những kỹ năng nền tảng quan trọng nhất phân biệt giữa một kỹ sư chỉ biết "khởi động lại service khi gặp lỗi" và một kỹ sư có khả năng "chẩn đoán và khắc phục sự cố tận gốc rễ".

Ghi nhớ 3 quy tắc vàng:
1. **`CLOSE-WAIT` là lỗi của ứng dụng tại chỗ** (không gọi `close()`). Cần sửa code ngay!
2. **`TIME-WAIT` là tính năng bảo vệ của TCP**, xuất hiện ở bên chủ động đóng trước. Nếu số lượng quá lớn, giải pháp là bật Connection Pooling và tái sử dụng socket!
3. **`SYN-SENT` là dấu hiệu của mạng bị chặn** (Firewall drop hoặc routing sai).

Hy vọng bài viết này đã giúp bạn tự tin làm chủ mọi trạng thái socket trong hệ thống Linux của mình!
