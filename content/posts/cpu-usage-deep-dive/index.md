---
title: "Giải mã toàn diện về CPU cho Lập trình viên: Cơ chế tính % CPU và bản chất thực sự của hiện tượng 'dùng hết 1 CPU'"
date: 2026-09-11T04:30:00+07:00
draft: false
description: "Phân tích chuyên sâu về CPU dưới lăng kính Lập trình viên: Bản chất chu kỳ xung nhịp và kiến trúc Cores/Hyper-Threading, cơ chế Linux Kernel đo lường CPU qua /proc/stat và Timer Interrupts, hiện tượng 'ngốn hết 1 CPU' trong Node.js, Python, Go, Java, và nghịch lý IPC (Instructions Per Cycle)."
summary: "CPU thực sự hoạt động thế nào? Hệ điều hành tính % CPU dựa trên nguyên lý gì? Tại sao chương trình ăn 100% CPU nhưng có thể đang... ngồi chơi xơi nước? Giải mã hiện tượng 'dùng hết 1 CPU' trên các nền tảng Node.js, Python, Go, Java và cẩm nang tối ưu hóa hiệu năng thực chiến."
tags: ["CPU", "Linux", "OS-Internals", "Performance", "Computer-Architecture", "Programming", "Concurrency", "Deep-Dive"]
categories: ["Hệ thống & Kernel", "Performance & SRE", "Kiến trúc máy tính"]
showTableOfContents: true
---

{{< katex >}}

Có bao giờ bạn rơi vào tình huống dở khóc dở cười này chưa:

Một ngày đẹp trời, hệ thống Production của bạn phát tín hiệu báo động đỏ. Khách hàng liên tục khiếu nại vì API phản hồi chậm chạp, thậm chí trả về lỗi `504 Gateway Timeout`. Bạn vội vàng mở bảng điều khiển giám sát (Datadog, Grafana hay Prometheus) và ngạc nhiên thấy: **Mức sử dụng CPU tổng thể của máy chủ chỉ vỏn vẹn 3.1% (trên một con máy chủ 32 Cores)!**

"Server còn rảnh tới 96.9%, làm sao mà nghẽn CPU được?", bạn tự nhủ. Nhưng khi SSH vào server và gõ lệnh `top`, bạn thấy một tiến trình Node.js hoặc Python duy nhất đang chễm chệ ở đỉnh bảng với con số:

```text
  PID USER      PR  NI    VIRT    RES    SHR S  %CPU  %MEM     TIME+ COMMAND
14285 nodejs    20   0 1250420 184512  32104 R 100.0   1.2   4:15.28 node /app/server.js
```

Con số `%CPU = 100.0%` kia thực chất mang ý nghĩa gì? 
- CPU là một con chip bán dẫn vật lý, nó không có cái "đồng hồ kim xăng" để đo xem mình đang đầy hay vơi. Vậy **con số 100% được hệ điều hành tính ra từ đâu?**
- Một chương trình "dùng hết 1 CPU" nghĩa là nó đang làm gì? Có phải nó đang xử lý tính toán với tốc độ bàn thờ?
- Tại sao một tiến trình Node.js chỉ cần ăn 100% của 1 core là có thể khiến **toàn bộ website tê liệt**, trong khi các ứng dụng viết bằng Go hay Java lại có thể ăn tới `400%`, `800%` CPU mà dịch vụ vẫn sống khỏe?
- Và đặc biệt: Tại sao có những chương trình ăn trọn 100% CPU, nhưng thực tế phần cứng thì CPU lại đang... **ngồi chơi xơi nước tới hơn 80% thời gian**?

Bài viết này sẽ đưa bạn đi một chuyến hành trình sâu vào bản chất của CPU dưới góc nhìn của một kỹ sư phần mềm: Từ kiến trúc bán dẫn, cơ chế tính toán thời gian (Time Accounting) bên trong Linux Kernel, cho đến cách các runtime hiện đại (Node.js, Python, Go, Java/C++) tương tác với CPU Scheduler.

---

## 1. Giải phẫu CPU dưới góc nhìn Lập trình viên

Trước khi tìm hiểu cách đo đạc `%CPU`, chúng ta cần hiểu chính xác đối tượng mà chúng ta đang đo là gì.

### 1.1. Chu kỳ xung nhịp (Clock Cycle): Nhịp tim của phần cứng

Để hiểu được vai trò của **Clock Cycle (Chu kỳ xung nhịp)**, hãy bắt đầu từ một hình ảnh đời thực rất trực quan:

#### 🥁 Hình tượng chiếc trống trên thuyền đua rồng
Hãy tưởng tượng một con thuyền rồng có 8 tay chèo. Nếu không có ai điều phối, người chèo nhanh người chèo chậm, các mái chèo sẽ va vào nhau làm thuyền chao đảo hoặc lật úp. Để thuyền lao đi nhanh nhất, bắt buộc phải có một người ngồi đầu thuyền gõ trống thật đều đặn:  
👉 **"TÙNG!... TÙNG!... TÙNG!..."**
- Cứ mỗi tiếng "TÙNG!" gõ xuống, tất cả các tay chèo **đồng loạt** thực hiện một nhịp: vung chèo → cắm xuống nước → đẩy nước → nhấc lên.

Trong một con chip CPU có hàng tỷ bóng bán dẫn (transistors), **Clock Cycle chính là tiếng trống đó**. Nó đóng vai trò là chiếc máy đánh nhịp (Metronome) đồng bộ hóa toàn bộ mạch điện tử, đảm bảo hàng tỷ linh kiện phối hợp nhịp nhàng mà không gây hỗn loạn.

#### ⚡ Tại sao CPU bắt buộc phải có Clock? (Độ trễ lan truyền điện tử)
Nhiều lập trình viên từng thắc mắc: *"Tại sao không để dòng điện chạy tự do cho nhanh nhất có thể, mắc mớ gì phải sinh ra bộ tạo xung để ngắt nhịp từng chu kỳ?"*

Câu trả lời nằm ở một quy luật vật lý: **Tín hiệu điện di chuyển trong vi mạch KHÔNG PHẢI là tức thời!**
- Khi CPU thực hiện một phép tính (ví dụ cộng 2 số nhị phân), dòng điện phải nạp và xả qua hàng chục tầng cổng logic bán dẫn (AND, OR, XOR).
- Quá trình này mất một khoảng thời gian nhất định (khoảng vài chục đến vài trăm picoseconds), gọi là **Độ trễ lan truyền (Propagation Delay)**.
- **Nếu không có Clock:** Tín hiệu ở cổng sau sẽ đọc kết quả khi cổng trước chưa tính xong, sinh ra **dữ liệu rác (Race conditions / Glitches)**.
- **Giải pháp:** Giữa các khối tính toán, nhà thiết kế chip đặt các thanh ghi chốt (**Flip-Flops / Latches**). Khi xung clock phát nhịp (Clock Edge), dữ liệu mới được phép đi vào mạch tính toán. Đến cuối chu kỳ, khi dòng điện đã chắc chắn ổn định ra kết quả chính xác, nhịp clock tiếp theo mới "chụp ảnh" và khóa kết quả lại để chuyển sang công đoạn kế tiếp.

#### ⏱️ Con số GHz thực chất nghĩa là gì?
CPU hoạt động theo nhịp đập của một tinh thể thạch anh dao động (Clock Generator). Khi bạn thấy thông số CPU ghi **3.0 GHz** (GigaHertz):
- \(1\text{ Hz} = 1\text{ nhịp / giây}\).
- \(3.0\text{ GHz} = 3\text{ tỷ nhịp / giây}\).
- Thời gian của đúng **1 Clock Cycle**:

$$
\text{Thời gian 1 chu kỳ} = \frac{1}{3,000,000,000\text{ Hz}} \approx 0.33\text{ nanoseconds (ns)}
$$

Trong tích tắc \(0.33\text{ ns}\) chớp nhoáng đó, ánh sáng chỉ kịp bay được khoảng 10 cm!

#### 🛠️ Dân lập trình cần quan tâm gì đến Clock Cycle?
Đối với kỹ sư phần mềm, **Clock Cycle chính là đơn vị tiền tệ định giá chi phí thực thi của từng câu lệnh**:

1. **Một dòng code tốn bao nhiêu Clock Cycles?**  
   Một dòng code cấp cao (như `c = a + b`) sẽ được biên dịch thành các chỉ lệnh Assembly máy, và mỗi chỉ lệnh máy lại có chi phí chu kỳ riêng:

| Lệnh Assembly máy | Ý nghĩa thao tác | Số Clock Cycles tiêu tốn |
| :--- | :--- | :--- |
| **`ADD`, `SUB`** | Cộng, trừ 2 số nguyên | **1 cycle** (~0.33 ns) |
| **`SHL`, `SHR`** | Dịch bit trái/phải | **1 cycle** (~0.33 ns) |
| **`IMUL`** | Nhân 2 số nguyên | **3 - 4 cycles** |
| **`IDIV`** | Chia 2 số nguyên | **15 - 40 cycles!** 🛑 |
| **L1 Cache Hit** | Đọc dữ liệu có sẵn trong L1 | **4 - 5 cycles** |
| **L2 / L3 Cache Hit** | Đọc dữ liệu trong L2 / L3 | **12 - 50 cycles** |
| **Main RAM (Cache Miss)** | Đọc RAM khi không có trong cache | **200 - 300 cycles!** 🛑 |

> 💡 **Bài học thực chiến:** Phép chia (`/`) tốn tới 30-40 chu kỳ xung nhịp, chậm hơn phép cộng (`+`) và phép dịch bit (`>>`) tới **30 đến 40 lần**! Đó là lý do tại sao các compiler luôn tự động tối ưu `x / 2` thành `x >> 1`.

2. **Dây chuyền sản xuất chỉ lệnh (Instruction Pipeline):**  
   Để thực thi trọn vẹn một lệnh, CPU chia nhỏ thành các công đoạn: *Fetch (Nạp lệnh) → Decode (Giải mã) → Execute (Thực thi) → Memory (Truy cập RAM/Cache) → Writeback (Ghi kết quả)*.  
   Nhờ có Clock Cycle làm nhịp gõ đều đặn, tại mỗi chu kỳ, tất cả các lệnh trong ống đều đồng loạt tịnh tiến thêm một bước:

{{< mermaid >}}
flowchart LR
    Fetch["1. Fetch<br/>(Nạp lệnh)"] --> Decode["2. Decode<br/>(Giải mã)"]
    Decode --> Execute["3. Execute<br/>(Tính trên ALU)"]
    Execute --> Memory["4. Memory Access<br/>(Đọc/Ghi Cache)"]
    Memory --> Writeback["5. Writeback<br/>(Ghi Register)"]

    style Fetch fill:#1e293b,stroke:#64748b,color:#fff
    style Decode fill:#1e3a8a,stroke:#3b82f6,color:#fff
    style Execute fill:#064e3b,stroke:#10b981,color:#fff
    style Memory fill:#7c2d12,stroke:#f97316,color:#fff
    style Writeback fill:#4c1d95,stroke:#a855f7,color:#fff
{{< /mermaid >}}

3. **Cú sốc vỡ đường ống (Branch Misprediction Stall):**  
   Khi gặp rẽ nhánh `if / else`, nếu CPU dự đoán sai nhánh sẽ chạy, nó buộc phải **hủy bỏ toàn bộ các lệnh đang nạp dở trong đường ống** và nạp lại từ đầu, làm lãng phí **15 đến 20 chu kỳ xung nhịp**! *(Đó là lý do tại sao duyệt mảng đã sắp xếp luôn chạy nhanh hơn mảng lộn xộn)*.

### 1.2. Phân biệt: Socket, Physical Core, Logical Core và vCPU

Đây là khu vực gây nhầm lẫn nhiều nhất khi cấu hình tài nguyên cho ứng dụng hoặc triển khai hạ tầng:

| Khái niệm | Tên gọi vật lý / logic | Bản chất phần cứng | Đặc điểm chia sẻ |
| :--- | :--- | :--- | :--- |
| **Socket** | CPU Package | Con chip vi xử lý vật lý cắm vào bo mạch chủ | Chứa 1 hoặc nhiều Cores, Memory Controller |
| **Physical Core** | Lõi vật lý | Một bộ xử lý độc lập hoàn chỉnh | Có ALU, FPU riêng, Register File riêng, Cache L1/L2 riêng |
| **Logical Core** | Luồng phần cứng (SMT / Hyper-Thread) | Nhân bản trạng thái kiến trúc (Registers, PC) trên 1 Core vật lý | **Dùng chung** ALU, FPU, Execution Pipeline và Cache của Lõi vật lý! |
| **vCPU** | Virtual CPU trên Cloud / VM | Thông thường tương đương với **1 Logical Core (Hardware Thread)** | Trên AWS EC2/GCP: 1 vCPU = 1 Hyper-thread (1/2 Core vật lý) |

{{< mermaid >}}
graph TD
    subgraph Physical_Socket["1 CPU Socket (Con chip vật lý)"]
        subgraph Core_0["Physical Core 0"]
            ALU0["ALU, FPU, Execution Units, L1/L2 Cache (DÙNG CHUNG)"]
            subgraph HT0["Hyper-Threading"]
                T0["Logical Core 0 (vCPU 0)<br/>(Registers + Program Counter 0)"]
                T1["Logical Core 1 (vCPU 1)<br/>(Registers + Program Counter 1)"]
            end
            T0 --- ALU0
            T1 --- ALU0
        end

        subgraph Core_1["Physical Core 1"]
            ALU1["ALU, FPU, Execution Units, L1/L2 Cache (DÙNG CHUNG)"]
            subgraph HT1["Hyper-Threading"]
                T2["Logical Core 2 (vCPU 2)<br/>(Registers + Program Counter 2)"]
                T3["Logical Core 3 (vCPU 3)<br/>(Registers + Program Counter 3)"]
            end
            T2 --- ALU1
            T3 --- ALU1
        end
    end

    style Physical_Socket fill:#0f172a,stroke:#38bdf8,stroke-width:2px,color:#fff
    style Core_0 fill:#1e293b,stroke:#64748b,color:#fff
    style Core_1 fill:#1e293b,stroke:#64748b,color:#fff
    style ALU0 fill:#7f1d1d,stroke:#ef4444,color:#fff
    style ALU1 fill:#7f1d1d,stroke:#ef4444,color:#fff
    style HT0 fill:#1e3a8a,stroke:#3b82f6,color:#fff
    style HT1 fill:#1e3a8a,stroke:#3b82f6,color:#fff
{{< /mermaid >}}

> [!IMPORTANT]
> **Sự thật về Hyper-Threading / SMT:**
> Khi công nghệ Hyper-Threading (Intel) hoặc SMT (AMD) được kích hoạt, 1 Core vật lý sẽ phân tách thành 2 Logical Cores. Hệ điều hành (Linux) sẽ nhìn thấy chúng như 2 CPU riêng biệt. 
> 
> Tuy nhiên, **chúng không hề có 2 bộ ALU tính toán riêng biệt!** Nếu cả hai tiến trình chạy trên `vCPU 0` và `vCPU 1` đều thực hiện các phép tính số học nặng (CPU-bound loop), chúng bắt buộc phải **chia phiên (interleave) dùng chung bộ ALU**. 
> 
> Vì vậy, bật Hyper-Threading chỉ tăng hiệu năng tổng thể từ **15% đến 30%** (nhờ tận dụng lúc luồng này chờ nạp dữ liệu từ RAM thì luồng kia mượn ALU để tính), chứ **không bao giờ gấp đôi hiệu năng (100%)** như nhiều người lầm tưởng!

### 1.3. Tháp bộ nhớ và "Bức tường bộ nhớ" (The Memory Wall)

CPU tính toán cực nhanh, nhưng nó không lưu trữ nhiều dữ liệu bên trong. Khi CPU cần nạp một biến từ RAM, khoảng cách tốc độ giữa bộ vi xử lý và thanh RAM là một thảm họa vật lý:

```text
Tầng lưu trữ                Thời gian truy cập (Độ trễ)      Tương quan đời thực (Nếu 1 Cycle = 1 Giây)
-------------------------------------------------------------------------------------------------------
CPU Registers               ~0.3 ns (1 cycle)                1 giây (Một cái chớp mắt)
L1 Cache (On-chip)          ~1 - 1.5 ns (3-4 cycles)         3 - 4 giây (Với tay lấy sách trên bàn)
L2 Cache (On-chip)          ~3 - 5 ns (10-14 cycles)         12 giây (Đứng dậy lấy sách trên giá)
L3 Cache (Shared)           ~15 - 20 ns (40-60 cycles)       1 phút (Đi ra phòng khách lấy đồ)
Main Memory (RAM DDR4/DDR5) ~60 - 100 ns (200-300 cycles)    4 - 5 phút (Đi bộ ra quán tạp hóa đầu ngõ)
NVMe SSD (I/O)              ~20,000 - 50,000 ns              Khoảng 1 tuần (Đi công tác xuyên quốc gia)
HDD Cơ học                  ~5,000,000 - 10,000,000 ns       Khoảng 2 - 3 tháng (Đi thuyền vòng quanh Trái Đất)
```

Khi một chỉ lệnh yêu cầu dữ liệu mà dữ liệu đó không có sẵn trong L1/L2/L3 Cache (gọi là **Cache Miss**), CPU buộc phải phát tín hiệu ra bus bộ nhớ và chờ đợi từ **200 đến 300 chu kỳ xung nhịp**.

Trong suốt 200 - 300 chu kỳ đó, các mạch tính toán ALU của CPU **hoàn toàn đứng bất động (Stall)**. Và đây chính là nguồn gốc của một trong những nghịch lý lớn nhất mà chúng ta sẽ phân tích ở Phần 4: **CPU báo 100% bận rộn nhưng thực chất đang đứng chờ nạp RAM!**

---

## 2. Cơ chế tính % CPU của Hệ điều hành (How CPU % is Calculated)

Bây giờ chúng ta bước vào câu hỏi then chốt: **Làm thế nào Linux Kernel biết được một tiến trình đang dùng bao nhiêu % CPU?**

### 2.1. CPU không có "thước đo phần trăm": Nguyên lý Tỷ lệ Thời gian

Ở mức điện tử bán dẫn, CPU là một linh kiện số rời rạc (discrete). Tại bất kỳ một tích tắc thời gian vô cùng nhỏ nào:
1. **Hoặc là CPU đang thực thi chỉ lệnh máy (Active / Running).**
2. **Hoặc là CPU đang ở trạng thái nghỉ (Idle):** Khi hệ điều hành không có bất kỳ tiến trình nào cần chạy trong hàng đợi `runqueue`, kernel sẽ nạp một tiến trình đặc biệt gọi là **Idle Task** (PID 0, thường gọi là `swapper`). Tiến trình này phát chỉ lệnh `HLT` (Halt trên x86) hoặc `WFI` (Wait For Interrupt trên ARM), khiến CPU ngắt bớt mạch điện tử, dừng xung clock của các execution unit để tiết kiệm điện và chờ đợi ngắt phần cứng tiếp theo.

CPU không bao giờ ở trạng thái "chạy 50% sức lực". Nó chỉ có: **Đang chạy** hoặc **Đang nghỉ**.

> [!NOTE]
> **Định luật nền tảng:**
> **`%CPU` KHÔNG PHẢI là cường độ tính toán hay công suất điện của CPU, mà là TỶ LỆ THỜI GIAN CPU Ở TRẠNG THÁI BẬN RỘN TRONG MỘT KHOẢNG THỜI GIAN QUAN SÁT (Time Accounting).**
>
> $$
> \%CPU = \frac{\text{Thời gian CPU Bận (Active Time)}}{\text{Tổng thời gian thực tế trôi qua (Wall-clock Time)}} \times 100\%
> $$

### 2.2. Nhịp tim của Kernel: Timer Interrupts, Ticks và Jiffies

Để đo đạc thời gian, hệ điều hành Linux dựa vào một bộ tạo xung phần cứng (Hardware Timer / APIC timer) được lập trình để phát ra các tín hiệu ngắt định kỳ lên CPU, gọi là **Timer Interrupt**.

Mỗi lần ngắt xảy ra được gọi là một **Tick**.
- Tần số ngắt được cố định khi biên dịch kernel qua tham số `CONFIG_HZ` (thường là 100Hz, 250Hz, hoặc 1000Hz trên Linux x86).
- Nếu `CONFIG_HZ = 250`: Cứ mỗi \(\frac{1000\text{ms}}{250} = 4\text{ms}\), phần cứng sẽ "gõ cửa" CPU một lần.
- Kernel duy trì một biến toàn cục đếm số lượng tick từ khi khởi động máy, gọi là **`jiffies`**.

{{< mermaid >}}
sequenceDiagram
    autonumber
    participant HW as Hardware Timer (Clock)
    participant CPU as CPU Core
    participant Kernel as Linux Kernel Scheduler
    participant Proc as User Process (App)

    HW->>CPU: Phát tín hiệu ngắt Timer Interrupt (1 Tick = 4ms)
    CPU->>Kernel: Tạm dừng app, chuyển sang Kernel Mode
    Note over Kernel: Kernel kiểm tra trạng thái CPU tại tích tắc này:<br/>1. Đang chạy code ứng dụng? -> Tăng user tick<br/>2. Đang chạy syscall? -> Tăng system tick<br/>3. Đang ở HLT? -> Tăng idle tick
    Kernel->>Kernel: Cập nhật jiffies & /proc/stat
    Kernel->>Proc: Khôi phục ngữ cảnh (Context) & tiếp tục chạy
{{< /mermaid >}}

Tại mỗi nhịp Timer Interrupt:
1. Kernel Scheduler kiểm tra xem trên Core hiện tại, tiến trình nào đang chiếm quyền điều khiển.
2. Nếu CPU đang thực thi mã ở User Space → Kernel cộng 1 tick vào trường **`user`**.
3. Nếu CPU đang thực thi trong Kernel Space (đang xử lý một System Call như `read()`, `write()`, `epoll_wait()`) → Kernel cộng 1 tick vào trường **`system`**.
4. Nếu CPU đang chạy tiến trình Idle Task → Kernel cộng 1 tick vào trường **`idle`** (hoặc **`iowait`** nếu hệ thống đang có tiến trình chờ đĩa).

> [!TIP]
> **Tickless Kernel (`NO_HZ`):**
> Trên các bản Linux Kernel hiện đại, để tiết kiệm năng lượng, kernel sử dụng chế độ `CONFIG_NO_HZ_IDLE` hoặc `CONFIG_NO_HZ_FULL`. Khi một core rơi vào trạng thái Idle, kernel sẽ **tắt luôn Timer Interrupt** trên core đó để CPU ngủ sâu. 
> 
> Khi CPU thức dậy, kernel sẽ đọc trực tiếp thanh ghi đếm chu kỳ phần cứng siêu chính xác **TSC (Time Stamp Counter)** để tính xem CPU đã ngủ bao nhiêu nanoseconds, sau đó quy đổi ngược lại jiffies. Bản chất phép tính vẫn là đo lường thời gian!

### 2.3. Bóc tách cơ chế tính toán trong `/proc/stat` (Toàn hệ thống)

Tất cả các công cụ giám sát như `top`, `htop`, `vmstat`, Prometheus `node_exporter` đều đọc dữ liệu thô từ file ảo `/proc/stat`.

Hãy xem thử một dòng trong `/proc/stat`:

```bash
$ cat /proc/stat | grep '^cpu '
cpu  2255 34 2290 22625 180 0 54 0 0 0
```

10 con số này biểu thị tổng số `ticks` (jiffies) tích lũy kể từ lúc máy khởi động:

1. **`user` (2255)**: Thời gian thực thi ở User Space với độ ưu tiên bình thường.
2. **`nice` (34)**: Thời gian thực thi ở User Space với độ ưu tiên thấp (giá trị `nice` dương).
3. **`system` (2290)**: Thời gian chạy trong Kernel Space (xử lý syscall, context switch).
4. **`idle` (22625)**: Thời gian CPU không làm gì cả (chạy `HLT`).
5. **`iowait` (180)**: Thời gian CPU nhàn rỗi NHƯNG có ít nhất một tiến trình đang bị khóa chờ I/O đĩa hoặc mạng NFS.
6. **`irq` (0)**: Thời gian xử lý ngắt phần cứng (Hardware Interrupts).
7. **`softirq` (54)**: Thời gian xử lý ngắt phần mềm (Software Interrupts - ví dụ xử lý gói tin mạng nhận về).
8. **`steal` (0)**: Thời gian bị Hypervisor (KVM, Xen, VMware) "cướp" mất CPU để phục vụ máy ảo khác.
9. **`guest` (0)**: Thời gian chạy một máy ảo khách (Guest OS).
10. **`guest_nice` (0)**: Thời gian chạy máy ảo khách với độ ưu tiên nice.

#### Thuật toán tính % CPU của lệnh `top`:
Giả sử tại thời điểm \(t_1\), `top` đọc được hàng số liệu trên. Sau đó \(t_2\) (sau đúng 1 giây), `top` đọc lại `/proc/stat` lần thứ hai:

$$
\Delta \text{Total} = \Delta user + \Delta nice + \Delta system + \Delta idle + \Delta iowait + \Delta irq + \Delta softirq + \Delta steal
$$

$$
\Delta \text{Busy} = \Delta \text{Total} - (\Delta idle + \Delta iowait)
$$

$$
\% \text{CPU Usage} = \frac{\Delta \text{Busy}}{\Delta \text{Total}} \times 100\%
$$

### 2.4. Bóc tách cơ chế tính CPU của một tiến trình (`/proc/[PID]/stat`)

Khi bạn muốn biết tiến trình PID `14285` tốn bao nhiêu % CPU, công cụ giám sát đọc file `/proc/14285/stat`:

```bash
$ cat /proc/14285/stat
14285 (node) R 1 14285 ... 1284 312 0 0 ...
```

Trong hơn 50 trường của file này, có hai trường quan trọng nhất:
- **Trường số 14 (`utime`)**: Số clock ticks mà tiến trình này đã chạy ở User Space.
- **Trường số 15 (`stime`)**: Số clock ticks mà tiến trình này đã chạy ở Kernel Space (thay mặt nó xử lý syscall).

Công thức tính `%CPU` của một tiến trình cụ thể trong khoảng thời gian \(\Delta \text{Wall Time}\):

$$
\Delta \text{Process Ticks} = (utime_{t2} - utime_{t1}) + (stime_{t2} - stime_{t1})
$$

$$
\text{Process CPU Seconds} = \frac{\Delta \text{Process Ticks}}{\text{CLK\_TCK}}
$$

*(Trong đó `CLK_TCK` thường là 100 ticks/giây đối với user space)*

$$
\% \text{CPU}_{\text{Process}} = \frac{\text{Process CPU Seconds}}{\Delta \text{Wall Time}} \times 100\%
$$

### 2.5. Chế độ Irix vs Solaris trong lệnh `top`

Một câu hỏi kinh điển: **"Tại sao trên máy 8 cores, tôi thấy một tiến trình có `%CPU = 400%` hoặc `800%`?"**

Đó là vì lệnh `top` trên Linux mặc định chạy ở **Chế độ Irix (Irix Mode)**:
- Mỗi Core vật lý / vCPU được tính là **100%**.
- Nếu máy chủ của bạn có 8 Cores, tổng công suất tối đa của tất cả các cores cộng lại là **800%**.
- Một tiến trình có 4 threads chạy hết công suất trên 4 cores khác nhau sẽ hiển thị: `%CPU = 400%`.

Nếu bạn bấm phím tắt **`Shift + I`** khi đang mở `top`, nó sẽ chuyển sang **Chế độ Solaris (Solaris Mode)**:
- Tổng công suất của toàn bộ máy chủ được chuẩn hóa về **100%**.
- Tiến trình chạy hết công suất 4 cores ở trên sẽ hiển thị: \(\frac{400\%}{8 \text{ Cores}} = \mathbf{50\%}\).

### 2.6. Cơ chế CPU trong Container (Docker / Kubernetes cgroups)

Trong môi trường Cloud Native, Kubernetes không cấp "Core vật lý" riêng biệt cho Pod, mà dùng hệ thống **cgroups (Control Groups)** của Linux Kernel thông qua cơ chế **CFS Bandwidth Control**:

Hai thông số cốt lõi:
- **`cpu.cfs_period_us`**: Độ dài của một chu kỳ thời gian (mặc định là \(100,000\,\mu\text{s} = 100\text{ms}\)).
- **`cpu.cfs_quota_us`**: Tổng thời lượng CPU tối đa mà container được phép sử dụng trong chu kỳ đó.

Ví dụ: Bạn cấu hình trong Kubernetes Pod:
```yaml
resources:
  limits:
    cpu: "1.5"   # Tương đương 1.5 vCPU
```

Kernel sẽ cấu hình cgroups:
- `period` = \(100,000\,\mu\text{s}\) (100ms)
- `quota` = \(1.5 \times 100,000 = 150,000\,\mu\text{s}\) (150ms)

```text
Mô phỏng: Container có limit: 1.0 CPU (Quota = 100ms trong Period = 100ms) chạy 2 Threads

Thời gian thực (Wall Time):  0ms               50ms                            100ms
                             |------------------|--------------------------------|
Thread 1 (Core 0)            [==== CHẠY 50ms ===] [        BỊ CFS THROTTLE       ]
Thread 2 (Core 1)            [==== CHẠY 50ms ===] [     (ĐÓNG BĂNG HOÀN TOÀN)   ]
                             |------------------|--------------------------------|
Tổng CPU Time đã dùng:       0ms               100ms (HẾT HẠN NGẠCH QUOTA!)
Trạng thái Container:        [    HOẠT ĐỘNG    ] [   NGHẼN, TIMEOUT, TĂNG LATENCY]
```

{{< mermaid >}}
flowchart TD
    subgraph CFS["Chu kỳ CFS Period = 100ms (Hạn mức Quota = 100ms)"]
        direction TB
        subgraph Phase1["Giai đoạn 0ms - 50ms (Đang có Quota)"]
            T1["Thread 1: Tiêu thụ 50ms CPU"]
            T2["Thread 2: Tiêu thụ 50ms CPU"]
            T1 & T2 --> Sum["Tổng CPU Time: 50ms + 50ms = 100ms<br/>(HẾT SẠCH HẠN NGẠCH QUOTA!)"]
        end

        subgraph Phase2["Giai đoạn 50ms - 100ms (Bị Throttling)"]
            Throttle["LINUX KERNEL CFS THROTTLE<br/>(Đóng băng Container)"]
            Impact["- Không thể xử lý thêm HTTP request<br/>- P99 Latency tăng vọt đột biến<br/>- CPU trung bình cả phút chưa tới 100% nhưng dịch vụ bị đơ!"]
            Throttle --> Impact
        end

        Phase1 -->|"Hết quota lúc 50ms"| Phase2
        Phase2 -->|"Hết chu kỳ 100ms"| Reset["Cấp lại Quota 100ms cho chu kỳ mới"]
    end

    style CFS fill:#0f172a,stroke:#38bdf8,stroke-width:2px,color:#fff
    style Phase1 fill:#1e293b,stroke:#10b981,stroke-width:1px,color:#fff
    style T1 fill:#064e3b,stroke:#10b981,color:#fff
    style T2 fill:#064e3b,stroke:#10b981,color:#fff
    style Sum fill:#7f1d1d,stroke:#ef4444,stroke-width:2px,color:#fff
    style Phase2 fill:#3b0764,stroke:#a855f7,stroke-width:1px,color:#fff
    style Throttle fill:#7f1d1d,stroke:#ef4444,stroke-width:2px,color:#fff
    style Impact fill:#1e293b,stroke:#94a3b8,color:#fff
    style Reset fill:#064e3b,stroke:#10b981,color:#fff
{{< /mermaid >}}

> [!WARNING]
> **Hiểm họa CPU Throttling:**
> Nếu container của bạn chạy đa luồng (ví dụ 4 threads) và cùng lúc tính toán nặng, nó có thể tiêu thụ hết toàn bộ 150ms quota chỉ trong vòng 37.5ms đầu tiên của chu kỳ!
> 
> Trong 62.5ms còn lại của chu kỳ, Linux Kernel sẽ **ép dừng hoàn toàn (Throttled)** container đó. Container sẽ không thể nhận hay xử lý bất kỳ gói tin mạng nào cho đến khi chu kỳ tiếp theo bắt đầu. Hậu quả là: **P99 Latency tăng đột biến, request timeout hàng loạt dù nhìn mức sử dụng CPU trung bình cả phút vẫn chưa chạm đỉnh!**

---

## 3. "Chương trình dùng hết 1 CPU" nghĩa là sao?

Sau khi đã hiểu bản chất đo đạc theo thời gian, chúng ta có thể đưa ra định nghĩa chính xác nhất về mặt hệ điều hành:

> [!IMPORTANT]
> **Định nghĩa chuẩn xác:**
> Một chương trình "dùng hết 1 CPU" (100% của 1 core / vCPU) có nghĩa là: **Một thread của chương trình đó liên tục có công việc tính toán và không hề tự nguyện nhường CPU trong suốt khoảng thời gian quan sát. Trong mỗi 1 giây thời gian thực trôi qua, thread này tích lũy đủ 1 giây CPU Execution Time.**

### 3.1. Chuyện gì xảy ra ở tầng OS Scheduler?

Tại sao một chương trình ngốn 100% của 1 core nhưng máy tính của bạn không bị "đơ đơ giật lag" hoàn toàn?

Đó là nhờ cơ chế **Preemptive Multitasking (Đa nhiệm cướp quyền)** của Linux Kernel thông qua bộ điều phối **CFS (Completely Fair Scheduler)** hoặc **EEVDF (Earliest Eligible Virtual Deadline First)**:

1. **Không có chương trình nào được chạy vĩnh viễn:** Kernel gán cho mỗi thread một lát thời gian (Time-slice, thường từ 0.75ms đến 6ms).
2. **Cướp quyền điều khiển (Preemption):** Khi hết lát thời gian, Timer Interrupt gõ cửa. Kernel can thiệp, lưu toàn bộ giá trị các thanh ghi của thread đó vào bộ nhớ (Context Switch), và nhường CPU cho thread khác có độ ưu tiên cao hơn hoặc có thời gian chạy ảo (`vruntime`) thấp hơn.
3. Nhưng nếu trên core đó **không có bất kỳ tiến trình nào khác cần chạy**, Kernel Scheduler lại lập tức trao lại quyền kiểm soát cho thread đó!
4. Kết quả: Thread đó chiếm trọn 100% thời gian của core đó.

### 3.2. Năm thủ phạm kinh điển khiến chương trình ăn trọn 100% CPU

Dưới đây là các kịch bản thực tế mà các lập trình viên thường xuyên gặp phải:

#### 1. Vòng lặp rỗng / Busy Waiting (Tight Loop)
```c
// Ví dụ bằng C: Đốt cháy 100% CPU ngay lập tức
int main() {
    while (1) {
        // Không sleep, không I/O syscall -> Chiếm trọn CPU Time-slice
    }
    return 0;
}
```
Khi chạy đoạn mã này, thread liên tục thực thi chỉ lệnh nhảy `JMP` trong bộ nhớ đệm L1i. Vì không có bất kỳ lệnh gọi hệ thống (I/O syscall) hay lệnh `sleep()` nào để tự nguyện nhường CPU (`sched_yield()`), thread này sẽ vét sạch từng microsecond CPU được cấp phát.

#### 2. Tính toán nặng (CPU-bound Algorithm)
- **Mã hóa / Băm dữ liệu:** Tính toán SHA-256, bcrypt, scrypt, verify chữ ký số RSA/ECDSA, hoặc thiết lập bắt tay TLS/SSL liên tục.
- **Serialization / Deserialization khổng lồ:** Parse một chuỗi JSON dung lượng 100MB bằng JavaScript hoặc Python. Việc duyệt qua hàng triệu ký tự, ép kiểu số, cấp phát hàng triệu đối tượng trong bộ nhớ sẽ đẩy 1 core lên 100% trong vài giây.
- **Nén dữ liệu:** Chạy thuật toán gzip, zstd, brotli ở mức nén cao nhất (compression level 9).

#### 3. Thảm họa ReDoS (Regular Expression Denial of Service)
Một biểu thức chính quy ngây thơ có thể đánh sập một thread server. Xét ví dụ regex kiểm tra chuỗi:

```python
import re
# Regex có cấu trúc lồng nhau nguy hiểm (Nested Quantifiers)
pattern = re.compile(r"^(a+)+$")

# Chuỗi đầu vào không khớp ở ký tự cuối cùng
payload = "a" * 30 + "X"

# Dòng này sẽ chiếm 100% của 1 CPU trong nhiều phút vì Catastrophic Backtracking!
pattern.match(payload)
```
Engine Regex dạng NFA (Non-deterministic Finite Automaton) sẽ phải quay lui (backtrack) qua \(2^{30}\) nhánh kết hợp để thử so khớp trước khi kết luận là thất bại. Một thread bị kẹt cứng ở 100% CPU!

#### 4. Spinlock & Lock Contention ở tầng thấp
Trong lập trình đa luồng hiệu năng cao, thay vì đưa thread vào trạng thái ngủ (Mutex/Futex Sleep - tốn chi phí Context Switch), lập trình viên đôi khi sử dụng **Spinlock**:
```cpp
// Vòng lặp quay cuồng kiểm tra cờ khóa
while (lock.exchange(true, std::memory_order_acquire)) {
    // Busy wait: CPU chạy vòng lặp liên tục chờ khóa được mở
    #if defined(__x86_64__)
    _mm_pause(); // Giảm tải nhẹ cho pipeline nhưng CPU vẫn ở trạng thái bận
    #endif
}
```
Nếu luồng giữ khóa bị trễ hoặc chết, các luồng đang chờ sẽ biến thành những cỗ máy thiêu rụi 100% CPU mà không tạo ra bất kỳ giá trị công việc hữu ích nào.

#### 5. Garbage Collection (GC) Thrashing
Khi bộ nhớ Heap của ứng dụng (Java JVM, Go, Node.js) bị đầy tới 98-99%, Garbage Collector sẽ hoảng loạn kích hoạt các chu kỳ quét dọn liên tục (Concurrent Mark-Sweep, Full GC). Các worker thread của GC sẽ lùng sục khắp bộ nhớ để tìm từng byte rác. Ứng dụng dường như "đóng băng", và `%CPU` vọt lên 100% cho mỗi core mà GC thread được phép chạy.

---

## 4. Nghịch lý lớn: "100% CPU" chưa chắc là "CPU đang làm việc cật lực"!

Đây là bí mật đắt giá nhất mà chỉ các kỹ sư Performance và SRE dày dạn kinh nghiệm mới để ý:

> [!CAUTION]
> **Chỉ số %CPU chỉ đo THỜI GIAN CPU bận rộn, chứ không đo KHỐI LƯỢNG CÔNG VIỆC mà CPU hoàn thành!**

### 4.1. Khái niệm IPC (Instructions Per Cycle)

Để biết CPU có đang thực sự "lao động hiệu quả" hay không, chúng ta phải đo chỉ số **IPC (Instructions Per Cycle - Số chỉ lệnh thực thi thành công trong một chu kỳ xung nhịp)**.

Một CPU hiện đại (như Intel Core thế hệ mới hoặc AMD Zen) là kiến trúc siêu vô hướng (**Superscalar Architecture**), có khả năng thực thi đồng thời nhiều lệnh trong một chu kỳ (IPC lý thuyết có thể đạt từ 3.0 đến 4.0).

Hãy so sánh hai chương trình sau, cả hai đều báo **`%CPU = 100%`** trên lệnh `top`:

```text
Kịch bản A: Nhân 2 ma trận số học lớn nằm gọn trong L1 Cache (Compute-bound)
--------------------------------------------------------------------------------
- Lệnh perf stat đo được:
  Cycles:        3,000,000,000 cycles / giây (100% Core clock)
  Instructions:  7,500,000,000 instructions / giây
  => IPC:        2.50 (RẤT TỐT - CPU đang tính toán hết công suất!)


Kịch bản B: Duyệt một Linked List khổng lồ 2GB nằm rải rác trên RAM (Memory-bound)
--------------------------------------------------------------------------------
- Lệnh perf stat đo được:
  Cycles:        3,000,000,000 cycles / giây (100% Core clock)
  Instructions:    600,000,000 instructions / giây
  => IPC:        0.20 (THẢM HỌA - CPU đang bị Memory Stall!)
```

{{< mermaid >}}
pie title Phân bổ chu kỳ xung nhịp trong Kịch bản B (IPC = 0.2)
    "Đứng chờ dữ liệu từ RAM (Memory Stall)" : 80
    "Chờ giải mã chỉ lệnh (Frontend Stall)" : 12
    "Thời gian tính toán thực tế (Retiring)" : 8
{{< /mermaid >}}

Trong Kịch bản B:
- Bộ điều khiển CPU liên tục gặp **Cache Miss**.
- Mỗi lần miss, CPU phải đứng đợi RAM từ 200 - 300 chu kỳ.
- Trong mắt Linux Kernel, CPU vẫn đang "bận rộn" phục vụ tiến trình đó (chưa hết time-slice, không yield). Kernel vẫn cộng tick vào `user time`, và `top` vẫn hùng dũng báo **`100.0% CPU`**!
- Nhưng trên thực tế: **80% thời gian của con chip là ngồi chơi xơi nước, chờ đợi các electron di chuyển từ thanh RAM về!**

> [!TIP]
> **Bài học cho Lập trình viên:**
> Viết code có cấu trúc dữ liệu thân thiện với Cache (**Data Locality** - ví dụ dùng mảng phẳng liên tục `std::vector`, `ArrayList` thay vì danh sách liên kết con trỏ `LinkedList`) có thể giúp chương trình chạy nhanh hơn gấp **5 đến 10 lần** dù cả hai cách viết đều ngốn cùng một mức `%CPU = 100%`!

---

## 5. Sự khác biệt sống còn giữa các Ngôn ngữ & Runtime

Mỗi ngôn ngữ lập trình có một mô hình quản lý luồng (Concurrency Model) riêng biệt. Hiểu được điều này sẽ giải thích vì sao cùng là "ngốn hết 1 CPU", hậu quả của nó lại hoàn toàn khác nhau.

### 5.1. Node.js / JavaScript: Đòn chí mạng vào Single Thread

Node.js sử dụng mô hình **Single-threaded Event Loop** (được cung cấp bởi thư viện `libuv` và V8 Engine). Mặc dù Node.js có một Thread Pool ngầm bên dưới để xử lý I/O đĩa hoặc mã hóa crypto, nhưng **toàn bộ mã JavaScript của bạn chỉ chạy trên DUY NHẤT 1 Main Thread**.

{{< mermaid >}}
flowchart TD
    subgraph Single_Thread["Main JavaScript Thread (Ghim trên 1 Core)"]
        Request1["HTTP Request 1: Tính toán vòng lặp nặng (CPU-bound)"]
        Block["ĐANG CHIẾM 100% CPU CỦA CORE ĐÓ!"]
        Queue["Hàng đợi Event Loop (BỊ KHÓA CHẶT)"]
        Request2["HTTP Request 2 (Chờ được phục vụ)"]
        Request3["HTTP Request 3 (Chờ được phục vụ)"]
        Timer["Timer Callback setTimeout (Bị kẹt)"]

        Request1 --> Block
        Block -.->|Chưa nhường CPU| Queue
        Queue --> Request2
        Queue --> Request3
        Queue --> Timer
    end

    style Single_Thread fill:#1e293b,stroke:#ef4444,stroke-width:2px,color:#fff
    style Block fill:#7f1d1d,stroke:#ef4444,color:#fff
    style Queue fill:#374151,stroke:#9ca3af,color:#fff
{{< /mermaid >}}

**Hệ quả khi Node.js ăn 100% của 1 CPU:**
- Giả sử máy chủ của bạn có **64 Cores**.
- Một hacker gửi một payload JSON độc hại khiến đoạn code JS chạy vòng lặp tốn 10 giây.
- Tiến trình Node.js ngốn trọn vẹn **100% của Core số 0**.
- Lệnh `top` nhìn tổng quan hệ thống: Mức sử dụng CPU toàn máy chỉ có:

$$
\frac{100\%}{64 \text{ Cores}} \approx 1.56\%
$$

- **Nhưng toàn bộ HTTP Server coi như đã chết lâm sàng!** Không một request nào khác có thể được Event Loop tiếp nhận hay trả lời. Tất cả các kết nối socket mới đều bị timeout. 63 cores còn lại hoàn toàn bất lực đứng nhìn vì Node.js không tự phân tán mã JS sang core khác được!

**Giải pháp cho Node.js:**
- Không bao giờ chạy các tác vụ nặng (nén ảnh, parse file lớn, hash mật khẩu phức tạp) trực tiếp trên Main Thread.
- Sử dụng **`worker_threads`** module để đẩy tác vụ sang luồng nền.
- Triển khai mô hình đa tiến trình với **Node.js Cluster Module** hoặc dùng Process Manager như **PM2** (chạy \(N\) tiến trình tương ứng với \(N\) cores).

### 5.2. Python (CPython): Sự kìm kẹp của chiếc khóa GIL

Python hỗ trợ tạo nhiều luồng thông qua thư viện `threading`. Tuy nhiên, trình thông dịch mặc định CPython có một cơ chế nổi tiếng: **GIL (Global Interpreter Lock)**.

GIL là một chiếc khóa tương hỗ (mutex) ngăn cản nhiều OS threads thực thi bytecode Python cùng một lúc, nhằm đảm bảo an toàn bộ nhớ cho cơ chế Reference Counting của CPython.

```python
# Kịch bản: Máy có 8 Cores, mở 4 threads chạy tính toán số học
import threading

def cpu_burner():
    while True:
        pass

for _ in range(4):
    t = threading.Thread(target=cpu_burner)
    t.start()
```

Khi chạy đoạn script trên trên máy Linux 8 Cores:
- Bạn mong đợi 4 threads sẽ chạy trên 4 cores và đẩy `%CPU` lên `400%`?
- **Thực tế trên `top`:** Mức sử dụng CPU của tiến trình Python chỉ dao động quanh ngưỡng **`100% - 105%`**!
- Bốn luồng hệ điều hành tranh giành nhau chiếc khóa GIL điên cuồng. Tại bất kỳ một thời điểm nào, chỉ có DUY NHẤT 1 luồng được giữ GIL để thực thi chỉ lệnh Python trên 1 core. Các luồng còn lại phải xếp hàng chờ. Thậm chí hiệu năng còn tệ hơn chạy đơn luồng do chi phí tranh chấp lock (GIL contention)!

**Giải pháp cho Python:**
- Với tác vụ CPU-bound, **tuyệt đối không dùng `threading`**. Phải dùng module **`multiprocessing`** hoặc `concurrent.futures.ProcessPoolExecutor` để tạo các tiến trình độc lập với không gian bộ nhớ riêng.
- Sử dụng các thư viện C-extension giải phóng GIL khi tính toán (như NumPy, SciPy, PyTorch).
- Đón chờ Python 3.13+ với chế độ **Free-threaded Python (PEP 703)** cho phép tắt hoàn toàn GIL.

### 5.3. Golang: Bộ điều phối Goroutines M:N và Asynchronous Preemption

Golang tiếp cận bài toán theo cách hiện đại hơn rất nhiều với mô hình điều phối **M:N Scheduler** (ánh xạ \(M\) goroutines siêu nhẹ lên \(N\) luồng hệ điều hành OS threads):
- Biến môi trường `GOMAXPROCS` mặc định bằng đúng số Cores CPU của máy.
- Nếu máy có 8 cores, Go runtime sẽ tạo ra 8 OS Threads (Machine `M`) gắn liền với 8 bộ xử lý ảo (Processor `P`).

{{< mermaid >}}
graph TD
    subgraph Go_Runtime["Go M:N Scheduler (GOMAXPROCS = 2)"]
        P0["Processor P0 (Gắn với OS Thread M0 trên Core 0)"]
        P1["Processor P1 (Gắn với OS Thread M1 trên Core 1)"]
        
        G1["Goroutine G1 (Tính toán nặng)"]
        G2["Goroutine G2"]
        G3["Goroutine G3 (Tính toán nặng)"]
        G4["Goroutine G4"]
        
        P0 --> G1
        P0 -.Hàng đợi local.-> G2
        P1 --> G3
        P1 -.Hàng đợi local.-> G4
    end

    style Go_Runtime fill:#0f172a,stroke:#00add8,stroke-width:2px,color:#fff
    style P0 fill:#1e293b,stroke:#38bdf8,color:#fff
    style P1 fill:#1e293b,stroke:#38bdf8,color:#fff
    style G1 fill:#7f1d1d,stroke:#ef4444,color:#fff
    style G3 fill:#7f1d1d,stroke:#ef4444,color:#fff
{{< /mermaid >}}

**Điều gì xảy ra khi 1 Goroutine rơi vào vòng lặp vô tận?**
- **Trước Go 1.14 (Cooperative Preemption):** Go chỉ nhường quyền thực thi (preempt) tại các điểm gọi hàm (dựa vào việc kiểm tra cờ stack growth). Nếu bạn viết một vòng lặp kín không gọi bất kỳ hàm nào (`for {}`), goroutine đó sẽ chiếm trọn OS thread `M` đó mãi mãi. Nếu bạn có 8 vòng lặp như vậy, toàn bộ 8 OS threads bị chiếm sạch, Go Runtime tê liệt!
- **Từ Go 1.14 trở đi (Asynchronous Preemption):** Go runtime sử dụng tín hiệu hệ điều hành **`SIGURG`** để ngắt ngang luồng đang chạy sau mỗi 10ms. Tín hiệu này ép goroutine đang chạy vòng lặp phải nhường bộ xử lý `P` cho các goroutine khác. Dịch vụ vẫn tiếp tục hoạt động!
- Nếu bạn chạy 4 Goroutines tính toán nặng trên máy 4 cores, Go sẽ tự động phân bổ chúng ra 4 OS threads, và `top` sẽ hiển thị **`%CPU = 400%`** một cách mượt mà.

### 5.4. Java / C++ / Rust: Mô hình 1:1 OS Threading

Trong C++, Rust, và Java (trước thời đại Virtual Threads của Project Loom):
- Mỗi khi bạn gọi `new Thread()` trong Java hay `std::thread` trong C++, JVM/OS sẽ tạo ra một **POSIX Thread (pthread)** thực thụ của hệ điều hành.
- Nếu một thread bị kẹt ở vòng lặp 100% CPU:
  - Nó chỉ chiếm trọn **đúng 1 Core** mà nó đang được OS Scheduler cấp phát.
  - Các thread khác trong Thread Pool (như Tomcat worker threads, Netty event loops) vẫn được OS xếp lịch chạy trên các core còn lại một cách bình thường.
  - Tuy nhiên, nếu lượng request dồn dập và mã nguồn có lỗi khiến **mọi thread trong pool lần lượt rơi vào vòng lặp vô tận**, toàn bộ thread pool sẽ bị cạn kiệt, kéo theo toàn bộ các cores của server đều chạm mốc 100%. Lúc này hệ thống sẽ sụp đổ hoàn toàn.

---

## 6. Thực nghiệm thực chiến: Tính % CPU của 1 Hàm & Endpoint HTTP (Bình thường vs Performance Test)

Để biến toàn bộ lý thuyết trên thành kỹ năng thực tế, chúng ta hãy cùng xây dựng một kịch bản đo đạc cụ thể trên một ứng dụng backend thực tế.

### 6.1. Thiết kế Endpoint HTTP và Hàm tính toán CPU-bound

Xét một HTTP service (viết bằng Node.js Express) có một endpoint phục vụ băm dữ liệu bảo mật `/api/hash-token`:

```javascript
// server.js
const express = require('express');
const crypto = require('crypto');
const app = express();

// Hàm CPU-bound: Băm dữ liệu liên tục bằng SHA-256
function hashToken(payload) {
    let hash = payload;
    // Vòng lặp tính toán ngốn CPU chu kỳ
    for (let i = 0; i < 200_000; i++) {
        hash = crypto.createHash('sha256').update(hash).digest('hex');
    }
    return hash;
}

app.get('/api/hash-token', (req, res) => {
    const startCpu = process.cpuUsage();
    const startWall = Date.now();

    // Thực thi hàm tính toán
    const result = hashToken("my-secret-token-payload");

    // Đo đạc CPU time thực tế mà hàm này đã ngốn
    const cpuDiff = process.cpuUsage(startCpu);
    const wallDiff = Date.now() - startWall;

    const cpuTimeMs = (cpuDiff.user + cpuDiff.system) / 1000;

    res.json({
        result: result.substring(0, 16),
        wallTimeMs: wallDiff,
        cpuTimeMs: cpuTimeMs
    });
});

app.listen(3000, () => console.log('Server running on port 3000'));
```

Khi chạy hàm `hashToken()` đơn lẻ trên CPU 3.0 GHz:
- Hàm này tiêu tốn trung bình: **10ms CPU Execution Time** (chạy ở User space).
- Tương đương: \(10\text{ms} \times 3,000,000\text{ cycles/ms} = \mathbf{30,000,000\text{ chu kỳ xung nhịp (cycles)}}\).

---

### 6.2. Kịch bản 1: Khi hệ thống chạy bình thường (Normal Load)

Giả sử lượng truy cập rải rác: **5 requests mỗi giây (5 RPS)**.

#### Cơ chế tính toán của Linux Kernel trong 1 giây (\(1000\text{ms}\) Wall Time):
- Số request xử lý: 5.
- Tổng thời lượng CPU mà hàm `hashToken()` tiêu thụ trong 1 giây:

$$
\text{CPU Active Time} = 5 \text{ req} \times 10\text{ms} = \mathbf{50\text{ms}}
$$

- Tổng thời gian CPU nhàn rỗi (Idle Time):

$$
\text{CPU Idle Time} = 1000\text{ms} - 50\text{ms} = \mathbf{950\text{ms}}
$$

- Công thức tính % CPU hiển thị trên `top` hoặc Prometheus:

$$
\%CPU = \frac{\text{CPU Active Time}}{\text{Wall Time}} \times 100\% = \frac{50\text{ms}}{1000\text{ms}} \times 100\% = \mathbf{5.0\%}
$$

```text
Trục thời gian thực tế trong 1 giây (1000ms):
|== 10ms ==|..........|== 10ms ==|..........|== 10ms ==|..........|== 10ms ==|..........|== 10ms ==|....................|
  Req 1       Idle      Req 2       Idle      Req 3       Idle      Req 4       Idle      Req 5         Idle (HLT)
```

**Nhận xét:**
- Mức sử dụng CPU của tiến trình chỉ là **5%**.
- Trong **950ms còn lại**, CPU Core rơi vào trạng thái `HLT` (Idle) để tiết kiệm điện.
- Thời gian phản hồi (Latency) của mỗi request là **10ms**. Khách hàng trải nghiệm dịch vụ cực kỳ mượt mà.

---

### 6.3. Kịch bản 2: Khi chạy Performance Test (Stress Test với tải cao)

Bây giờ, chúng ta dùng công cụ benchmark chuyên dụng như **`autocannon`** (hoặc `wrk`, `k6`) để bơm tải liên tục với 50 kết nối đồng thời:

```bash
# Bắn tải đồng thời 50 kết nối trong 20 giây
autocannon -c 50 -d 20 http://localhost:3000/api/hash-token
```

#### Ngưỡng bão hòa vật lý (Capacity Limit) của 1 CPU Core:
Một Core CPU chỉ có tối đa **1000ms thời gian thực thi trong mỗi 1 giây thời gian thực**.
Nếu mỗi request bắt buộc phải tốn **10ms CPU time** để băm SHA-256, thì **thông lượng tối đa lý thuyết** mà 1 Core có thể xử lý là:

$$
\text{Max Throughput} = \frac{1000\text{ms CPU Time / giây}}{10\text{ms CPU Time / request}} = \mathbf{100\text{ requests / giây (RPS)}}
$$

#### Điều gì xảy ra khi công cụ test gửi tới 300 req/s hoặc 50 VUs liên tục?
1. **CPU chạm trần 100%:**  
   Trong mỗi 1 giây, Core CPU chạy không ngừng nghỉ từ millisecond 0 đến millisecond 1000:
   
   $$
   \%CPU = \frac{1000\text{ms CPU Time}}{1000\text{ms Wall Time}} \times 100\% = \mathbf{100.0\%}
   $$

2. **Chuyện gì xảy ra với các request vượt ngưỡng (từ request 101 trở đi)?**
   - Do CPU không còn 1 microsecond nào rảnh rỗi, các request gửi tới sau **không thể được xử lý ngay lập tức**.
   - Chúng bắt buộc phải nằm xếp hàng chờ đợi trong **Socket Listen Backlog** và hàng đợi Event Loop.
3. **Cú nổ độ trễ (Latency Explosion):**
   - Request số 1: Xử lý ngay → **Latency = 10ms**.
   - Request số 50: Phải chờ 49 request trước chạy xong → **Latency = 50 × 10ms = 500ms**.
   - Request số 200: Chờ 199 request trước → **Latency = 200 × 10ms = 2,000ms (2 giây)**!
   - **P99 Latency tăng vọt từ 10ms lên tới hàng nghìn ms**, thậm chí nhiều kết nối bị rớt (Connection Timeout) dù code không hề bị crash!

```text
Trục thời gian thực tế trong 1 giây khi Stress Test:
|============================== CHẠY 1000ms KHÔNG NGHỈ (100% CPU) ==============================|
Req 1 | Req 2 | Req 3 | ... | Req 99 | Req 100 | (KHÔNG CÒN SLOT NÀO NỮA!)
                                                 ↳ Các req còn lại dồn ứ trong Queue gây LAG P99!
```

---

### 6.4. Làm thế nào để đo % CPU của RIÊNG 1 HÀM trong toàn bộ ứng dụng?

Khi mở `top`, bạn chỉ biết được cả tiến trình `node` hay `go_app` đang ăn 100% CPU. Làm sao bạn chứng minh được trước cả team rằng: **"Chính hàm `hashToken()` đang chiếm 85% CPU của cả service"?**

Có 2 phương pháp chuẩn quốc tế:

#### Phương pháp 1: Lấy mẫu thống kê bằng `perf` (Statistical Sampling Profiling)
Linux Kernel hỗ trợ kỹ thuật lấy mẫu phần cứng bằng lệnh `perf`:

```bash
# Lấy mẫu với tần số 99 Hz (99 lần mỗi giây) trên tiến trình PID 14285
sudo perf record -F 99 -p 14285 -g -- sleep 10
```

**Nguyên lý hoạt động:**
- Cứ mỗi khoảng \(1 / 99 \approx 10.1\text{ms}\), Linux Kernel phát ra một ngắt lấy mẫu (Sampling Interrupt).
- Kernel "chụp ảnh" thanh ghi con trỏ chỉ lệnh **`RIP` (Instruction Pointer)** xem CPU lúc đó đang đứng ở hàm nào và lưu lại Call Stack.
- Nếu trong 10 giây ghi lại được tổng cộng **990 mẫu (samples)**:
  - Có **842 mẫu** con trỏ lệnh đang nằm trong thân hàm `hashToken()`.
  - Có **95 mẫu** nằm trong tầng mạng socket của `libuv` (`uv__io_poll`).
  - Có **53 mẫu** nằm trong bộ phân tích JSON (`v8::internal`).
- Khi bạn chạy `sudo perf report`, Linux sẽ tính toán:

$$
\% \text{CPU của hàm hashToken} = \frac{842 \text{ mẫu}}{990 \text{ mẫu}} \times 100\% \approx \mathbf{85.05\%}
$$

Bạn có bằng chứng rõ như ban ngày để tối ưu chính xác đoạn code cần sửa!

#### Phương pháp 2: Tự đo đạc CPU Time trực tiếp trong Code (Application Profiling)
Trong Node.js, bạn có thể tự giám sát xem một hàm tốn bao nhiêu % CPU trung bình mỗi phút mà không cần cài thêm công cụ ngoài:

```javascript
let totalFunctionCpuMicroseconds = 0;
let windowStartTime = Date.now();

function monitoredHashToken(payload) {
    const start = process.cpuUsage();
    
    // Gọi hàm thực tế
    const result = hashToken(payload);
    
    const diff = process.cpuUsage(start);
    // Cộng dồn CPU time (User + System) bằng microsecond
    totalFunctionCpuMicroseconds += (diff.user + diff.system);
    return result;
}

// Cứ mỗi 10 giây, tính toán % CPU mà hàm này đã chiếm
setInterval(() => {
    const elapsedWallMs = Date.now() - windowStartTime;
    const elapsedCpuMs = totalFunctionCpuMicroseconds / 1000;
    
    const funcCpuPercent = (elapsedCpuMs / elapsedWallMs) * 100;
    console.log(`[METRICS] Hàm hashToken chiếm: ${funcCpuPercent.toFixed(2)}% CPU trong 10s qua.`);
    
    // Reset cửa sổ đo
    totalFunctionCpuMicroseconds = 0;
    windowStartTime = Date.now();
}, 10000);
```

---

### 6.5. Bảng tổng hợp so sánh: Bình thường vs Performance Test

| Tiêu chí | Khi chạy bình thường (Normal) | Khi chạy Performance Test (Stress) |
| :--- | :--- | :--- |
| **Lưu lượng (Throughput)** | 5 requests / giây | 100 requests / giây *(Đạt trần vật lý)* |
| **Thời gian chạy hàm mỗi giây** | 50 ms CPU Time | 1000 ms CPU Time *(Vét sạch thời gian)* |
| **Thời gian Idle của Core** | 950 ms (95% thời gian nghỉ `HLT`) | 0 ms (Không có thời gian nghỉ) |
| **% CPU hiển thị trên `top`** | **~ 5.0%** | **100.0%** *(Trên 1 Core)* |
| **Thời gian phản hồi P50** | **~ 10 ms** | **~ 450 ms** |
| **Thời gian phản hồi P99** | **~ 12 ms** | **~ 2,500 ms - 5,000 ms** 🛑 |
| **Trạng thái Socket Queue** | Trống (Empty) | Dồn ứ hàng trăm request chờ |
| **Trạng thái Event Loop / Thread** | Rảnh rỗi đón request mới ngay lập tức | Tê liệt, block hoàn toàn các tác vụ khác |

---

## 7. Cẩm nang thực chiến chẩn đoán khi gặp sự cố 100% CPU

Khi nhận cảnh báo một tiến trình đang ngốn 100% CPU, bạn cần trang bị cho mình tư duy chẩn đoán bài bản theo từng bước sau:

### Bước 1: Phân biệt `%user` cao hay `%system` cao

Dùng lệnh `top` hoặc `pidstat -u 1` để quan sát tỷ lệ:

```bash
$ pidstat -u -p 14285 1
Linux 5.15.0 (prod-api)   09/11/2026   _x86_64_  (8 CPU)

09:15:01      UID       PID    %usr %system  %guest   %wait    %CPU   CPU  Command
09:15:02     1000     14285   98.50    1.50    0.00    0.00  100.00     3  node
```

- **Nếu `%usr` (User CPU) chiếm đa số (> 80%):** Vấn đề nằm hoàn toàn trong **mã nguồn ứng dụng** của bạn. Code đang kẹt ở vòng lặp vô tận, thuật toán có độ phức tạp cao \(O(N^2)\), parse JSON khổng lồ, hoặc ReDoS.
- **Nếu `%system` (System/Kernel CPU) chiếm tỷ lệ cao (> 30-40%):** Ứng dụng của bạn đang liên tục gọi các **System Call** vào Linux Kernel. Điển hình:
  - Cấp phát và giải phóng bộ nhớ liên tục ở tần suất khủng khiếp (`malloc` / `free` làm việc với page faults).
  - Tranh chấp khóa quá nặng khiến kernel liên tục phải đánh thức và cho ngủ các thread qua system call `futex`.
  - Đọc ghi socket/file ở kích thước buffer quá nhỏ (gọi hàng triệu syscall `read()` / `write()` 1 byte).

### Bước 2: Soi tận xương tủy ở cấp độ Thread (`top -H`)

Một tiến trình hiển thị 100% CPU không có nghĩa là tất cả các thread đều bận. Hãy xem chính xác **Thread ID (TID)** nào đang gây họa bằng cách thêm cờ **`-H`**:

```bash
$ top -H -p 14285
```

```text
  PID   TID USER      PR  NI    VIRT    RES    SHR S  %CPU  %MEM     TIME+ COMMAND
14285 14285 nodejs    20   0 1250420 184512  32104 R  99.8   1.2   6:12.45 node
14285 14286 nodejs    20   0 1250420 184512  32104 S   0.0   1.2   0:00.12 V8 WorkerThread
14285 14287 nodejs    20   0 1250420 184512  32104 S   0.0   1.2   0:00.15 V8 WorkerThread
```

Nhìn vào cột `TID`:
- `TID 14285` (chính là Main Thread) đang ăn **99.8% CPU**!
- Các worker thread khác đang ngủ (`S`). Chúng ta khẳng định 100%: **Main Event Loop đang bị nghẽn đồng bộ.**

*(Nếu trong ứng dụng Java, bạn lấy TID này đổi sang số Hexadecimal, rồi dùng lệnh `jstack <PID> | grep -A 20 <TID_HEX>` là sẽ thấy ngay chính xác dòng code Java nào đang thiêu rụi CPU!)*

### Bước 3: Đọc ngăn xếp cuộc gọi với `perf` và FlameGraph

Không cần phải đoán mò mã nguồn, Linux cung cấp công cụ hồ sơ hóa mạnh nhất hành tinh: **`perf`**.

Để xem hàm nào đang chiếm nhiều chu kỳ CPU nhất theo thời gian thực:

```bash
# Xem live profiling các hàm đang ngốn CPU
sudo perf top -p 14285
```

Màn hình sẽ hiển thị trực tiếp bảng tỷ lệ phần trăm theo từng hàm:
```text
  Samples: 35K of event 'cycles', 4000 Hz, Event count (approx.): 8521094411
  Overhead  Shared Object       Symbol
    68.42%  app.node            [.] my_expensive_hash_calculation
    18.15%  libv8.so            [.] v8::internal::Scavenger::ScavengePage
     8.21%  libc.so.6           [.] __memmove_avx_unaligned_erms
```

Nhìn vào đây, bạn thấy ngay: **Hàm `my_expensive_hash_calculation` đang chiếm tới 68.42% toàn bộ chu kỳ CPU của cả tiến trình!**

Nếu muốn có một bức tranh trực quan toàn cảnh, bạn có thể ghi lại mẫu và vẽ biểu đồ ngọn lửa (**FlameGraph**):

```bash
# Ghi lại call graph trong 10 giây
sudo perf record -F 99 -p 14285 -g -- sleep 10

# Tạo FlameGraph (sử dụng bộ công cụ của Brendan Gregg)
sudo perf script | ./stackcollapse-perf.pl | ./flamegraph.pl > cpu_flamegraph.svg
```

Ngọn lửa nào có phần đỉnh bẹt và rộng nhất trên biểu đồ FlameGraph chính là đoạn code đang nuốt trọn CPU của bạn.

---

## 8. Bảng tổng kết Checklist tối ưu hóa CPU cho Software Engineer

| Vấn đề phát hiện | Nguyên nhân gốc rễ (Root Cause) | Giải pháp tối ưu hóa kiến trúc |
| :--- | :--- | :--- |
| **Node.js ăn 100% của 1 core, server timeout** | Chạy tác vụ tính toán nặng, parse file lớn trên Main Thread | Đẩy sang `worker_threads`, chia nhỏ chunk bằng `setImmediate`, hoặc dùng Background Queue (RabbitMQ, Kafka, BullMQ) |
| **Python mở nhiều thread nhưng chỉ ăn 1 core** | Bị kìm kẹp bởi Global Interpreter Lock (GIL) | Chuyển sang dùng `multiprocessing`, `ProcessPoolExecutor`, hoặc viết module mở rộng bằng Rust/C |
| **CPU User (%usr) đạt 100% nhưng IPC < 0.5** | CPU bị Memory Stall do Cache Miss liên tục | Chuyển cấu trúc dữ liệu sang mảng phẳng liên tục (Array), tránh nhảy con trỏ ngẫu nhiên (Pointer Chasing), tối ưu Data Locality |
| **CPU System (%system) tăng vọt bất thường** | Quá nhiều System Calls, Context Switch hoặc tranh chấp lock (Futex) | Tăng buffer size khi đọc/ghi socket, gom batch request, dùng lock-free data structures |
| **Kubernetes Pod bị latency spike dù CPU chưa tới 100%** | Bị Linux Kernel CFS Throttling do cấu hình `limits.cpu` quá chặt | Tăng `limits.cpu`, tắt cpu limit chỉ dùng `requests.cpu`, hoặc nâng `cpu.cfs_period_us` |
| **Đột ngột ăn 100% CPU sau khi người dùng nhập chuỗi lạ** | Biểu thức Regex dính bẫy Catastrophic Backtracking (ReDoS) | Viết lại regex an toàn, kiểm tra độ dài input trước khi match, hoặc chuyển sang dùng engine regex DFA (như Google RE2) |

---

## Lời kết

Đối với một lập trình viên phần mềm, CPU không nên là một chiếc "hộp đen" huyền bí.

Con số `%CPU` trên màn hình giám sát không đơn thuần là một chỉ số phần trăm vô tri. Đằng sau nó là nhịp đập của những ngắt Timer Interrupt từng mili-giây, là những lát cắt thời gian được CFS Scheduler cân đo đong đếm, là sự tranh chấp từng chu kỳ xung nhịp giữa các luồng phần cứng Hyper-Threading, và là ranh giới mong manh giữa một thuật toán tính toán thông minh với một đoạn code đang làm CPU kiệt quệ vì đứng chờ nạp từng dòng bộ nhớ.

Hiểu rõ bản chất của CPU và cơ chế đo đạc của hệ điều hành sẽ giúp bạn không còn hoang mang khi đối mặt với những sự cố Production hóc búa, từ đó viết nên những dòng mã không chỉ đúng đắn về mặt logic, mà còn vận hành mượt mà, tối ưu và thấu hiểu tận cùng phần cứng bên dưới.
