---
title: "Giải mã toàn diện lệnh top trong Linux: Cẩm nang chẩn đoán hiệu năng hệ thống từ lý thuyết đến thực chiến SRE"
date: 2026-09-05T17:50:00+07:00
draft: false
description: "Hướng dẫn chuyên sâu và chi tiết nhất về lệnh top trong Linux: Giải mã bản chất Load Average, 8 chỉ số CPU (us, sy, wa, st...), bộ ba bộ nhớ VIRT/RES/SHR, 15 phím tắt quyền năng và 4 kịch bản thực chiến chẩn đoán nghẽn CPU, I/O và Thread trên Production."
summary: "Khám phá toàn bộ sức mạnh ẩn sau lệnh top kinh điển: Từ cơ chế kernel đằng sau Load Average và CPU metrics (wa, si, st), phân biệt VIRT vs RES vs SHR, đến kỹ thuật soi thread level và chẩn đoán sự cố nghẽn hệ thống dành cho DevOps & SRE."
tags: ["Linux", "top", "Performance", "Troubleshooting", "DevOps", "SRE", "Sysadmin", "Monitoring"]
categories: ["Linux", "Performance & SRE", "Troubleshooting"]
showTableOfContents: true
---

Khi một máy chủ Linux trên môi trường Production phát tín hiệu "kêu cứu" — API phản hồi chậm bất thường, CPU spike lên đỉnh 100%, hoặc hệ thống bị đơ nghẽn — hành động phản xạ đầu tiên của hầu hết các kỹ sư là mở terminal và gõ một lệnh quen thuộc:

```bash
top
```

Giao diện bảng điều khiển quen thuộc lập tức hiện ra với hàng loạt con số nhảy múa liên tục. Tuy nhiên, **sai lầm phổ biến của nhiều kỹ sư là chỉ nhìn lướt qua hai con số `%CPU` và `%MEM` của vài tiến trình đứng đầu, rồi vội vã bấm `q` để thoát ra hoặc bấm `k` để kill tiến trình**.

Đó là một sự lãng phí tài nguyên chẩn đoán vô cùng lớn! Lệnh `top` (thuộc bộ gói `procps-ng`) không đơn thuần là một công cụ xem tiến trình; nó là một **bảng chụp X-quang toàn diện sức khỏe hệ thống** mà Linux Kernel cung cấp cho bạn trong thời gian thực. Mỗi ký tự, mỗi chỉ số trên màn hình `top` — từ `wa`, `si`, `st` cho đến `VIRT`, `RES`, `SHR` hay trạng thái `D`, `Z` — đều ẩn chứa nguyên nhân gốc rễ (Root Cause) của những sự cố hệ thống phức tạp nhất.

Bài viết này sẽ đưa bạn đi sâu vào từng pixel của `top`: giải mã cỗ máy kernel bên dưới các chỉ số, khai phá 15 phím tắt "quyền năng" ít người biết, và hướng dẫn 4 kịch bản thực chiến gỡ rối sự cố Production như một SRE chuyên nghiệp.

---

## 1. Giải phẫu 5 dòng tóm tắt hệ thống (Header Summary Area)

Khi chạy `top`, nửa trên màn hình là khu vực thông tin tóm tắt tổng quan toàn hệ sinh thái phần cứng và kernel:

```text
top - 17:51:03 up  8:27,  1 user,  load average: 1.29, 0.59, 0.47
Tasks: 360 total,   1 running, 358 sleeping,   0 stopped,   1 zombie
%Cpu(s): 44.4 us,  5.1 sy,  0.0 ni, 49.5 id,  0.0 wa,  0.0 hi,  1.0 si,  0.0 st 
MiB Mem :  15179.7 total,   5047.2 free,   4927.7 used,   6245.2 buff/cache     
MiB Swap:   4096.0 total,   4096.0 free,      0.0 used.  10252.0 avail Mem 
```

Hãy cùng mổ xẻ tường tận từng dòng một.

---

### Dòng 1: Thời gian thực, Uptime và Bản chất Load Average

```text
top - 17:51:03 up  8:27,  1 user,  load average: 1.29, 0.59, 0.47
```

- **`top - 17:51:03`**: Thời gian hiện tại của hệ thống.
- **`up  8:27`**: Máy chủ đã hoạt động liên tục được 8 giờ 27 phút (Uptime).
- **`1 user`**: Số lượng phiên đăng nhập (login sessions) đang hoạt động.
- **`load average: 1.29, 0.59, 0.47`**: Tải trung bình của hệ thống trong **1 phút, 5 phút và 15 phút** gần nhất.

#### 💡 Bí mật lớn nhất về Load Average trong Linux:
Rất nhiều người lầm tưởng rằng `Load Average = 100%` đồng nghĩa với việc CPU bị quá tải 100%. **Đây là quan niệm hoàn toàn sai lầm!**

Trong Linux, Load Average không chỉ đo mức sử dụng CPU. Nó là **số lượng tiến trình trung bình đang cạnh tranh tài nguyên hệ thống**, bao gồm:
1. Các tiến trình ở trạng thái **`R` (Running / Runnable)**: Đang dùng CPU hoặc sẵn sàng chạy nhưng đang đợi CPU Scheduler cấp phát time-slice.
2. Các tiến trình ở trạng thái **`D` (Uninterruptible Sleep)**: Đang bị treo đợi tài nguyên phần cứng, điển hình nhất là **chờ đọc/ghi ổ đĩa (Disk I/O)** hoặc **chờ mạng NFS / Storage Network**.

{{< mermaid >}}
flowchart TD
    A["Tiến trình muốn thực thi"] --> B{"Phân loại trạng thái"}
    B -->|"Đang chạy hoặc xếp hàng chờ CPU"| C["Trạng thái R (Runnable)"]
    B -->|"Bị khóa chờ Disk I/O hoặc NFS"| D["Trạng thái D (Uninterruptible Sleep)"]
    
    C --> E["Được tính vào LOAD AVERAGE"]
    D --> E
    
    E --> F["Load Average = Trung bình số task (R + D) trong 1m, 5m, 15m"]

    style A fill:#1e293b,stroke:#64748b,color:#fff
    style C fill:#064e3b,stroke:#10b981,color:#fff
    style D fill:#7f1d1d,stroke:#ef4444,color:#fff
    style E fill:#1e3a8a,stroke:#3b82f6,stroke-width:2px,color:#fff
    style F fill:#312e81,stroke:#818cf8,stroke-width:2px,color:#fff
{{< /mermaid >}}

> ⚠️ **HỆ QUẢ THỰC CHIẾN:**
> Nếu ổ cứng của bạn bị hỏng hoặc kết nối NFS bị treo, hàng trăm tiến trình sẽ rơi vào trạng thái `D`. Lúc này, **Load Average có thể nhảy vọt lên 50.0 hoặc 100.0, dù mức sử dụng CPU thực tế chỉ có 2%!**

#### Công thức đánh giá tải theo số lượng CPU Cores:
Để biết một giá trị Load Average là "nặng" hay "nhẹ", bạn bắt buộc phải chia nó cho **tổng số CPU Cores**:

```text
Tải tương đối = Load Average / Số lượng CPU Cores
```

- **< 0.7 (70%):** Mức tải an toàn, hệ thống đáp ứng nhanh, còn nhiều dư địa chịu tải.
- **= 1.0 (100%):** Hệ thống đạt ngưỡng bão hòa hoàn hảo. Toàn bộ CPU Cores đều có việc làm, không ai rảnh rỗi và chưa có ai phải xếp hàng chờ đợi.
- **> 1.0:** Bắt đầu có tiến trình phải xếp hàng chờ đợi.
- **> 3.0 - 5.0:** Báo động đỏ! Hệ thống đang bị nghẽn nghiêm trọng, độ trễ phản hồi (latency) sẽ tăng vọt theo cấp số nhân.

**Cách phân tích xu hướng 3 mốc thời gian (1m, 5m, 15m):**
- `1.29, 0.59, 0.47`: Giá trị 1m > 5m > 15m ➔ **Tải đang tăng nhanh**, có thể hệ thống vừa đón đợt traffic spike hoặc một batch job nặng vừa kích hoạt.
- `0.47, 0.59, 1.29`: Giá trị 1m < 5m < 15m ➔ **Tải đang giảm dần**, tình hình đang dần ổn định trở lại.

---

### Dòng 2: Phân bổ trạng thái của Tasks (Tiến trình)

```text
Tasks: 360 total,   1 running, 358 sleeping,   0 stopped,   1 zombie
```

- **`total`**: Tổng số lượng tác vụ (tasks/processes) đang có trong hệ điều hành.
- **`running`**: Số tác vụ đang trực tiếp chiếm giữ CPU tại thời điểm lấy mẫu (thường dao động từ 1 đến số core CPU).
- **`sleeping`**: Số tác vụ đang "ngủ" (chờ sự kiện, I/O, timer...). Đa số tiến trình trong Linux ở trạng thái này.
- **`stopped`**: Số tác vụ bị tạm dừng (bị gửi cờ `SIGSTOP` hoặc bấm `Ctrl + Z` trong terminal).
- **`zombie` (`<defunct>`)**: Số lượng **tiến trình ma (Zombie Processes)**!

#### 🧟 Zombie Process thực sự là gì?
Khi một tiến trình con kết thúc (exit), toàn bộ bộ nhớ RAM và tài nguyên của nó đã được giải phóng. Tuy nhiên, bản ghi của nó trong **Process Table (Bảng tiến trình của Kernel)** vẫn được giữ lại để lưu giữ mã thoát (Exit Status) và thống kê tài nguyên, chờ tiến trình cha (Parent Process) gọi hệ thống hàm `wait()` hoặc `waitpid()` để đọc và dọn dẹp.

Nếu tiến trình cha bị lỗi (buggy code) và không bao giờ gọi `wait()`, tiến trình con đó sẽ biến thành **Zombie (`Z`)**.

> ❌ **HIỂU LẦM TAI HẠI:** Chạy lệnh `kill -9 <PID_Zombie>`!  
> Lệnh `kill -9` gửi tín hiệu `SIGKILL` để hủy một tiến trình đang sống. Nhưng Zombie là một **tiến trình ĐÃ CHẾT**, nó không có code hay tiến trình nào đang chạy để nhận signal cả!  
> 
> **Cách diệt Zombie duy nhất:**
> 1. Tìm PID của tiến trình cha (Parent PID - PPID):
>    ```bash
>    ps -ef | grep defunct
>    ```
> 2. Gửi tín hiệu `SIGCHLD` cho tiến trình cha để nhắc nó gọi hàm `wait()`:
>    ```bash
>    kill -s SIGCHLD <PPID>
>    ```
> 3. Nếu cha vẫn "lỳ lợm" không dọn, hãy `kill` tiến trình cha. Khi cha chết, tiến trình Zombie sẽ được Kernel chuyển giao quyền nuôi dưỡng cho tiến trình `init` (PID 1 / systemd). PID 1 sẽ tự động gọi `wait()` và dọn sạch xác Zombie ngay lập tức!

---

### Dòng 3: %Cpu(s) - 8 Góc khuất của Bộ vi xử lý

Đây là dòng quan trọng bậc nhất giúp bạn "bắt mạch" chính xác CPU đang bận vì nguyên nhân gì:

```text
%Cpu(s): 44.4 us,  5.1 sy,  0.0 ni, 49.5 id,  0.0 wa,  0.0 hi,  1.0 si,  0.0 st
```

| Chỉ số | Tên đầy đủ | Ý nghĩa bản chất | Khi nào bất thường? |
| :---: | :--- | :--- | :--- |
| **`us`** | User CPU time | Tỷ lệ CPU dành cho các tiến trình chạy trong **User Space** (không qua nice). Bao gồm code ứng dụng (Java, Python, Node.js, Go, Nginx, Database...). | Cao là bình thường nếu ứng dụng đang xử lý tải tính toán thực tế. |
| **`sy`** | System CPU time | Tỷ lệ CPU dành cho **Kernel Space** (xử lý các system calls `read()`, `write()`, `fork()`, context switching, memory page allocation...). | **> 20-30%**: Bất thường! Ứng dụng đang gọi quá nhiều syscall, lock contention nghiêm trọng, hoặc context switch liên tục. |
| **`ni`** | Nice CPU time | Tỷ lệ CPU dành cho các tiến trình User Space có độ ưu tiên thấp (`nice > 0`). | Thường thấy khi chạy các batch job nền, nén file, backup data. |
| **`id`** | Idle | Tỷ lệ CPU đang **rảnh rỗi hoàn toàn**, không có tác vụ nào cần làm. | Nếu `id` cao mà hệ thống phản hồi chậm ➔ Vấn đề nằm ở I/O, Lock hoặc Network! |
| **`wa`** | IO-Wait | Tỷ lệ thời gian CPU **nhàn rỗi NHƯNG đang phải chờ các thao tác I/O hoàn tất** (Disk I/O hoặc Network Storage). | **> 5-10%**: Ổ cứng (HDD/SSD) hoặc kết nối NFS/EBS đang là nút thắt cổ chai nghẽn cứng! |
| **`hi`** | Hardware IRQ | Tỷ lệ CPU xử lý các **ngắt phần cứng** (Hardware Interrupts) sinh ra trực tiếp từ linh kiện (Card mạng NIC, Disk Controller...). | Thường rất nhỏ (< 1%). |
| **`si`** | Software IRQ | Tỷ lệ CPU xử lý các **ngắt mềm** (Software Interrupts, daemon `ksoftirqd`), phần lớn dùng để xử lý đóng gói và giải nén packet mạng TCP/IP. | **> 10-15%**: Bão gói tin mạng (Packet Flood, SYN Flood, DDoS) hoặc traffic mạng cực kỳ lớn! |
| **`st`** | Steal Time | Tỷ lệ thời gian CPU của **Máy ảo (VM)** bị bộ điều khiển ảo hóa (Hypervisor) "cắt xén" để chia cho các máy ảo khác trên cùng máy chủ vật lý. | **> 5%**: Máy ảo của bạn đang bị ảnh hưởng bởi "hàng xóm ồn ào" (Noisy Neighbors) hoặc bị hết CPU Credit (AWS T2/T3/T4g). |

---

### Dòng 4 & 5: MiB Mem và MiB Swap - Bí ẩn bộ nhớ RAM

```text
MiB Mem :  15179.7 total,   5047.2 free,   4927.7 used,   6245.2 buff/cache     
MiB Swap:   4096.0 total,   4096.0 free,      0.0 used.  10252.0 avail Mem 
```

#### 🚨 Cảnh báo hiểu lầm: "RAM `free` còn ít quá, server sắp hết RAM rồi!"
Trên hệ điều hành Linux: **"A free RAM is a wasted RAM" (RAM để trống là RAM lãng phí)**.
Linux Kernel áp dụng chiến lược tận dụng tối đa lượng RAM đang rảnh rỗi để làm **`buff/cache`**:
- **Buffers:** Bộ đệm lưu trữ metadata và raw disk blocks.
- **Page Cache:** Bộ nhớ đệm lưu nội dung các file vừa đọc/ghi từ ổ cứng lên RAM. Khi ứng dụng đọc lại file đó, Linux trả kết quả ngay từ RAM với tốc độ hàng chục GB/s thay vì phải đọc từ ổ cứng!

Khi ứng dụng của bạn cần thêm RAM, **Kernel sẽ ngay lập tức giải phóng vùng `buff/cache` này để cấp cho ứng dụng trong tích tắc**.

{{< mermaid >}}
flowchart TD
    Total["Tổng RAM Hệ Thống (Total Mem)"]
    Total --> Used["Used: RAM ứng dụng thực sự chiếm dụng"]
    Total --> Free["Free: RAM trống tuyệt đối (chưa ai đụng)"]
    Total --> BuffCache["buff/cache: RAM làm Cache đĩa (Tự động nhả khi cần)"]
    
    Free --> Avail["DUNG LƯỢNG KHẢ DỤNG THỰC TẾ (avail Mem)"]
    BuffCache -->|"Thu hồi tức thì khi có request"| Avail

    style Total fill:#1e293b,stroke:#64748b,color:#fff
    style Used fill:#7f1d1d,stroke:#ef4444,color:#fff
    style Free fill:#0f766e,stroke:#14b8a6,color:#fff
    style BuffCache fill:#1e3a8a,stroke:#3b82f6,color:#fff
    style Avail fill:#064e3b,stroke:#10b981,stroke-width:3px,color:#fff
{{< /mermaid >}}

> 📌 **CHỈ SỐ SỐNG CÒN CẦN NHÌN:**
> Đừng nhìn `free`! Hãy nhìn vào **`avail Mem` (Available Memory)** ở cuối dòng Swap:  
> `10252.0 avail Mem` chính là lượng RAM thực tế mà bạn có thể cấp phát cho các tiến trình mới **ngay lập tức mà không khiến hệ thống bị ép vào Swap hay rơi vào thảm họa OOM Killer**!

#### Khi nào sử dụng Swap là nguy hiểm?
- Nếu `used Swap > 0` nhưng tỷ lệ đọc/ghi swap (swap in/swap out qua lệnh `vmstat 1` cột `si`/`so`) bằng 0: **Hoàn toàn vô hại**. Kernel chỉ đơn giản là đẩy các vùng nhớ "chết" của các tiến trình ngủ hàng tuần ra Swap để nhường RAM xịn cho Page Cache.
- Nếu `used Swap` liên tục nhảy số, đi kèm `si`/`so` cao và `wa` (iowait) tăng vọt: **Thảm họa Tráo đổi bộ nhớ (Memory Thrashing)**! Hệ thống đang quằn quại đọc ghi ổ đĩa liên tục chỉ để tráo đổi bộ nhớ, dẫn tới toàn bộ server bị "đơ" (freeze).

---

## 2. Giải phẫu Bảng tiến trình (Process Table Area)

Nửa dưới màn hình `top` là danh sách chi tiết các tiến trình:

```text
    PID USER      PR  NI    VIRT    RES    SHR S  %CPU  %MEM     TIME+ COMMAND
 133987 vbox      20   0 2918728 378896 133144 S 166.7   2.4  78:14.03 agy
 115149 vbox      20   0 2134940 151168 102336 S  25.0   1.0   5:48.40 ptyxis
   7438 root      20   0 2030416 689104 161924 S   8.3   4.4  38:37.81 k3s-server
```

### 1. Phân biệt độ ưu tiên: `PR` vs `NI`
- **`NI` (Nice Value):** Mức độ "tử tế" (user-controlled priority) của tiến trình đối với các tiến trình khác, có dải giá trị từ **`-20` (Ưu tiên cao nhất, ít tử tế nhất)** đến **`19` (Ưu tiên thấp nhất, tử tế nhất nhường CPU)**. Giá trị mặc định là `0`.
- **`PR` (Kernel Priority):** Độ ưu tiên thực tế mà Kernel Scheduler sử dụng để lập lịch CPU.
  - Với tiến trình thông thường: `PR = 20 + NI`. Khi bạn đặt `NI = 0` thì `PR = 20`. Nếu hạ `NI = -5` thì `PR = 15`.
  - Với tiến trình Real-time: Cột PR sẽ hiển thị chữ `rt`.

---

### 2. Bộ ba kích thước bộ nhớ: `VIRT` vs `RES` vs `SHR`

Đây là chủ đề gây nhức đầu nhất khi phỏng vấn Senior DevOps/SRE:

{{< mermaid >}}
flowchart LR
    subgraph VIRT["VIRT (Virtual Memory) - Tổng địa chỉ ảo đã map"]
        subgraph RES["RES (Resident Set Size) - RAM vật lý thực tế"]
            SHR["SHR (Shared Mem)<br/>Shared Libs, libc.so"]
            PRIV["Private RSS<br/>Heap, Stack riêng của Process"]
        end
        ALLOC["Malloc chưa dùng, Swap, Files mmap chưa đọc"]
    end

    style VIRT fill:#1e293b,stroke:#64748b,color:#fff
    style RES fill:#064e3b,stroke:#10b981,stroke-width:2px,color:#fff
    style SHR fill:#1e3a8a,stroke:#3b82f6,color:#fff
    style PRIV fill:#0f766e,stroke:#2dd4bf,color:#fff
    style ALLOC fill:#334155,stroke:#94a3b8,stroke-dasharray: 5 5,color:#fff
{{< /mermaid >}}

1. **`VIRT` (Virtual Memory Size):**
   - Tổng không gian địa chỉ bộ nhớ ảo mà tiến trình đã đăng ký với Kernel.
   - Bao gồm: Mã nhị phân thực thi, heap, stack, toàn bộ các thư viện động (dynamic libraries), các file ánh xạ qua `mmap()`, và **cả những vùng nhớ đã gọi `malloc()` nhưng chưa hề ghi dữ liệu vào (chưa cấp trang RAM vật lý)**!
   - *Ví dụ:* Một ứng dụng JVM cấu hình `-Xms16G -Xmx16G` sẽ có `VIRT` ngay lập tức nhảy lên trên 16GB, dù nó mới dùng thực tế vài trăm MB.

2. **`RES` (Resident Set Size):**
   - **LƯỢNG RAM VẬT LÝ THỰC TẾ** mà tiến trình đang trực tiếp chiếm giữ trong các thanh RAM của máy chủ.
   - Đây là con số quan trọng nhất phản ánh việc tiến trình có đang ngốn RAM thật hay không.
   - *Lưu ý:* Cơ chế OOM Killer của Linux dựa chủ yếu vào `RES` để quyết định "trảm" tiến trình nào khi cạn kiệt bộ nhớ.

3. **`SHR` (Shared Memory Size):**
   - Phần bộ nhớ có thể được chia sẻ với các tiến trình khác (ví dụ: các thư viện chuẩn C `libc.so`, vùng nhớ chia sẻ IPC, file shared memory).

> 💡 **Mẹo tính bộ nhớ độc quyền (Private Memory):**
> Dung lượng RAM độc chiếm mà tiến trình thực sự "ôm riêng" (không chia sẻ với ai) xấp xỉ bằng:
> ```text
> Private RSS = RES - SHR
> ```

---

### 3. Cột `S` (Trạng thái tiến trình - Process Status)

- **`R` (Running):** Tiến trình đang trực tiếp thực thi trên CPU hoặc nằm trong runqueue của CPU.
- **`S` (Sleeping - Interruptible):** Tiến trình đang ngủ chờ tài nguyên (chờ socket có data, chờ timer, chờ user input). Có thể đánh thức bằng signals.
- **`D` (Uninterruptible Sleep):** Tiến trình ngủ sâu chờ phần cứng I/O (Disk, Device driver). **Không thể đánh thức hay kill bằng bất kỳ signal nào, kể cả `kill -9`!**
- **`Z` (Zombie):** Tiến trình ma đã chết nhưng cha chưa dọn xác.
- **`T` (Traced / Stopped):** Tiến trình bị dừng bởi tín hiệu (`Ctrl+Z`, `SIGSTOP`) hoặc đang bị debug bởi `gdb`/`strace`.
- **`I` (Idle):** Các luồng kernel thread nhàn rỗi (Kernel Task).

---

### 4. Tại sao `%CPU` lại lớn hơn `100%`?
Trong Linux, mỗi **CPU Core (hoặc vCPU) tương đương với 100%**.  
Nếu máy chủ của bạn có 16 Cores:
- Tổng công suất CPU tối đa là **`1600%`**.
- Nếu một tiến trình đa luồng (multi-threaded) như JVM, MySQL hay Go service xử lý song song trên 4 cores, `top` sẽ hiển thị `%CPU = 400%`.

---

## 3. 15 Phím tắt "quyền năng" giúp bạn làm chủ `top` như Pro

Đừng chỉ nhìn màn hình một cách thụ động. Khi `top` đang chạy, hãy dùng các phím tương tác sau để biến nó thành một công cụ phân tích siêu tốc:

| Phím tắt | Tác dụng thực chiến |
| :---: | :--- |
| **`1`** (Số một) | **Hiển thị chi tiết từng CPU Core (Per-CPU View):** Chuyển từ dòng tổng hợp `%Cpu(s)` sang `%Cpu0`, `%Cpu1`, `%Cpu2`... Cực kỳ cần thiết để phát hiện lỗi **Single-Core Bottleneck** (1 core 100% trong khi các core khác 0%). |
| **`Shift + P`** (`P`) | **Sắp xếp theo %CPU** (Chế độ mặc định). |
| **`Shift + M`** (`M`) | **Sắp xếp theo %MEM** (Dung lượng RAM). Phím số 1 khi server có dấu hiệu sắp OOM. |
| **`Shift + T`** (`T`) | **Sắp xếp theo TIME+** (Thời gian CPU tích lũy). Giúp tìm ra tiến trình chạy ngầm ăn mòn tài nguyên bền bỉ qua nhiều ngày. |
| **`Shift + N`** (`N`) | **Sắp xếp theo PID** theo thứ tự giảm dần/tăng dần. |
| **`Shift + E`** (`E`) | **Đổi đơn vị Header:** Chuyển đổi hiển thị dòng Mem/Swap từ KiB ➔ MiB ➔ GiB ➔ TiB ➔ PiB. Dễ đọc số RAM ngay lập tức mà không phải đếm số 0. |
| **`e`** (thường) | **Đổi đơn vị Process Table:** Chuyển đổi cột `VIRT`, `RES`, `SHR` sang KiB, MiB, GiB! |
| **`c`** | **Bật/Tắt Full Command Line:** Hiển thị toàn bộ đường dẫn thực thi và các tham số truyền vào (`/usr/bin/python3 app.py --port=8000`) thay vì chỉ hiện tên ngắn `python3`. |
| **`H`** (Hoa) | **Bật chế độ Thread (LWP Mode):** Hiển thị từng Thread riêng biệt thay vì gom chung vào Process. Vô giá khi debug ứng dụng Java, Go, C++. |
| **`V`** (Hoa) | **Hiển thị dạng cây (Forest View):** Hiển thị quan hệ cha-con (Parent-Child hierarchy) giữa các tiến trình trực quan như lệnh `pstree`. |
| **`u`** | **Lọc theo User:** Nhập tên user (ví dụ: `nginx`, `postgres`, `vbox`) để chỉ hiển thị các tiến trình thuộc sở hữu của user đó. |
| **`k`** | **Kill tiến trình ngay trong top:** Nhập PID cần kill, sau đó nhập mã signal (mặc định là `15` - SIGTERM, hoặc nhập `9` - SIGKILL). |
| **`r`** | **Renice tiến trình:** Nhập PID và giá trị nice mới (-20 đến 19) để điều chỉnh độ ưu tiên tức thì. |
| **`d`** hoặc **`s`** | **Thay đổi chu kỳ làm mới (Refresh Delay):** Mặc định là 3.0s. Có thể đổi thành `1` (1 giây) hoặc `0.5` để theo dõi biến động nhanh. |
| **`Shift + W`** (`W`) | **LƯU CẤU HÌNH:** Lưu toàn bộ thiết lập hiện tại (màu sắc, cột sắp xếp, đơn vị MB/GB...) vào file `~/.toprc`. Lần sau mở `top` lên sẽ giữ nguyên như vậy! |

---

## 4. 4 Kịch bản thực chiến gỡ rối sự cố Production

### Kịch bản 1: Load Average cao vút (30.0) nhưng %CPU lại thấp tè (< 10%)

- **Hiện tượng:** Cảnh báo Nagios/Prometheus báo Load Average vượt ngưỡng đỏ, server phản hồi cực kỳ ì ạch. Nhưng khi mở `top`, dòng `%Cpu(s)` báo `id` (idle) vẫn còn 85-90%!
- **Điều tra bằng `top`:**
  1. Nhìn vào cột **`wa` (IO-Wait)** ở dòng `%Cpu(s)`: Thấy `wa = 45.0%` hoặc cao hơn.
  2. Nhìn vào cột **`S`** ở danh sách tiến trình: Phát hiện hàng loạt tiến trình mang chữ **`D` (Uninterruptible Sleep)**.
- **Bản chất vấn đề:** CPU hoàn toàn không bị quá tải. Thủ phạm là **hệ thống ổ đĩa (Storage I/O)**. Ổ cứng bị quá tải IOPS, SSD bị giảm tuổi thọ, hoặc phân vùng mạng NFS/EBS bị timeout khiến hàng chục tiến trình bị kẹt cứng chờ đọc/ghi đĩa.
- **Lệnh điều tra tiếp theo:**
  ```bash
  # Xem tiến trình nào đang ngốn IO đĩa nhiều nhất
  sudo iotop -oP

  # Kiểm tra độ trễ và %util của từng ổ đĩa
  iostat -x 1 5
  ```

---

### Kịch bản 2: Máy ảo Cloud bị lag giật dù CPU mới chạm 40% (Bão Steal Time)

- **Hiện tượng:** Bạn thuê một VPS hoặc EC2 Instance trên Cloud (AWS, GCP, DigitalOcean). Vào giờ cao điểm, ứng dụng chậm chạp bất thường dù CPU chưa dùng hết một nửa.
- **Điều tra bằng `top`:**
  1. Nhìn vào chỉ số **`st` (Steal Time)** ở dòng `%Cpu(s)`: Thấy `st = 25.0%` hay thậm chí `50.0%`!
- **Bản chất vấn đề:**
  - Nhà cung cấp Cloud (hoặc Hypervisor KVM/VMware) đang dùng chung một CPU vật lý cho nhiều máy ảo khác nhau (Overcommit).
  - Các máy ảo "hàng xóm" trên cùng physical node đang chạy tác vụ quá nặng, khiến Hypervisor tước đoạt tài nguyên CPU mà đáng lẽ máy ảo của bạn được hưởng.
  - Trên AWS EC2 dòng T-Series (`t3.medium`, `t4g.large`), điều này xảy ra khi bạn đã **dùng cạn CPU Credits tích lũy**, instance bị bóp nghẽn hiệu năng xuống mức baseline.
- **Hướng xử lý:**
  - Nâng cấp gói instance sang dòng Dedicated CPU (như AWS Compute Optimized C-series, Memory Optimized R-series) thay vì dòng Burstable T-series.
  - Liên hệ nhà cung cấp Cloud yêu cầu di chuyển VM sang Physical Host khác nếu gặp tình trạng Noisy Neighbor kéo dài.

---

### Kịch bản 3: Ứng dụng Java / Go ngốn 400% CPU, tìm chính xác Thread nào gây tội?

Khi một tiến trình Java JVM (`java -jar app.jar`) chiếm trọn 400% CPU, lệnh `top` thông thường chỉ chỉ điểm đúng 1 dòng `PID = 12345 COMMAND = java`. Bạn không thể biết phương thức hay đoạn code nào đang lặp vô tận.

Hãy kích hoạt **kỹ thuật soi Thread-Level kết hợp `top` và Thread Dump**:

```bash
# Bước 1: Mở top ở chế độ xem Thread cho riêng PID bị lỗi
top -H -p 12345
```

- Bảng `top` lúc này sẽ tách toàn bộ các thread (LWP - Light Weight Process) của ứng dụng Java ra từng dòng.
- Bấm `Shift + P` để sắp xếp: Bạn sẽ thấy ngay Thread ID (TID) đang ngốn 99% CPU (ví dụ TID = `12480`).

```bash
# Bước 2: Chuyển đổi TID (hệ thập phân) sang hệ Hex (thập lục phân)
printf "%x\n" 12480
# Kết quả ra: 30c0

# Bước 3: Xuất Thread Dump của JVM và grep theo mã hex vừa tìm được
jstack 12345 | grep -A 20 "nid=0x30c0"
```

Bạn sẽ nhìn thấy chính xác: Tên Thread, Stack Trace, tên Class và chính xác **số dòng code (Line Number)** đang gây ra vòng lặp vô tận!

---

### Kịch bản 4: Chạy `top` ở chế độ Batch Mode (`top -b`) để tự động hóa

Bạn có biết `top` có thể chạy ngầm mà không cần giao diện tương tác? Chế độ **Batch Mode (`-b`)** là vũ khí tuyệt vời để ghi log và giám sát tự động:

```bash
# Chụp nhanh 1 lần trạng thái top, lấy top 10 tiến trình ăn CPU nhất và ghi ra file
top -b -n 1 | head -n 20 > /tmp/system_snapshot.txt

# Lọc nhanh các tiến trình ăn CPU > 50% bằng top batch mode
top -b -n 1 | awk 'NR>7 && $9 > 50.0 {print $1, $2, $9"%", $12}'
```

Bạn có thể đưa lệnh này vào Cronjob hoặc script debug để tự động lưu vết hệ thống mỗi khi có cảnh báo CPU tăng vọt lúc nửa đêm!

---

## 5. So sánh `top` với các công cụ giám sát thế hệ mới

Trong thế giới Linux hiện đại, chúng ta có rất nhiều lựa chọn thay thế hào nhoáng hơn `top`:

| Tiêu chí | `top` (procps-ng) | `htop` | `btop` / `bottom` | `atop` |
| :--- | :--- | :--- | :--- | :--- |
| **Tính phổ biến** | **100% có mặt sẵn** trên mọi bản phân phối Linux, Container, Alpine, Minimal OS. | Phổ biến, nhưng phải cài đặt thêm (`apt install htop`). | Rất đẹp, hiện đại, đồ thị bắt mắt nhưng phải cài qua snap/cargo. | Chuyên sâu về ghi log lịch sử hiệu năng hệ thống. |
| **Tiêu tốn tài nguyên** | **Cực thấp (< 5MB RAM)**, chạy mượt ngay cả khi server sắp sập. | Thấp (~15-30MB RAM). | Trung bình (~50-100MB RAM), render đồ họa phức tạp. | Rất thấp, chạy nền dạng daemon. |
| **Giao diện & Thao tác** | Bàn phím thuần túy, ký tự ASCII tối giản. | Hỗ trợ chuột, cuộn ngang/dọc, phím F1-F10 trực quan. | Đồ họa ASCII/Unicode đa màu sắc, biểu đồ thời gian thực cực đẹp. | Giao diện dòng lệnh cổ điển. |
| **Thời điểm nên dùng** | **Cứu hộ khẩn cấp (Emergency)**, Production tối giản, Container rỗng không có internet để cài tool mới. | Giám sát tương tác hàng ngày khi làm việc trên dev/staging server. | Máy trạm cá nhân (Workstation), màn hình Dashboard hiển thị NOC. | Điều tra sự cố trong quá khứ (Post-Mortem Analysis). |

> 🏆 **LỜI KHUYÊN TỪ CHUYÊN GIA:**  
> Bạn có thể yêu thích vẻ đẹp đầy màu sắc của `htop` hay `btop` trên máy cá nhân. Nhưng khi bạn phải SSH vào một **Container Alpine tối giản trên Kubernetes** hoặc một **Server Production bị cô lập mạng (Air-gapped) đang hấp hối**, bạn sẽ thấy: **Chỉ có duy nhất `top` luôn ở đó đợi bạn!** Thành thạo `top` là kỹ năng sinh tồn bắt buộc của mọi kỹ sư Linux thực thụ.

---

## 6. Bảng tra cứu phím tắt nhanh (Cheat Sheet)

Để tiện ghi nhớ khi thao tác trên terminal, hãy lưu lại bảng tra cứu nhanh dưới đây:

```text
=============================================================================
                      BẢNG PHÍM TẮT THỰC CHIẾN LỆNH TOP
=============================================================================
 [Hiển thị & Đơn vị]
   1          : Bật / Tắt chế độ xem chi tiết từng Core CPU (Per-CPU view)
   Shift + E  : Đổi đơn vị Header RAM/Swap (KiB -> MiB -> GiB -> TiB)
   e          : Đổi đơn vị Process Memory trong bảng (KiB -> MiB -> GiB)
   c          : Bật / Tắt hiển thị toàn bộ Command Path và Arguments
   H          : Bật / Tắt chế độ xem từng Thread riêng biệt (Thread Mode)
   V          : Hiển thị cây tiến trình cha - con (Forest Tree view)
   z          : Bật / Tắt hiển thị màu sắc (Color mode)

 [Sắp xếp dữ liệu]
   Shift + P  : Sắp xếp theo %CPU (Mặc định)
   Shift + M  : Sắp xếp theo %MEM (Sử dụng RAM)
   Shift + T  : Sắp xếp theo TIME+ (Thời gian chạy tích lũy)
   Shift + N  : Sắp xếp theo số hiệu PID

 [Tác vụ quản trị]
   u          : Lọc danh sách theo tên người dùng (User)
   k          : Gửi tín hiệu tiêu diệt tiến trình (Kill Signal: 15 hoặc 9)
   r          : Thay đổi độ ưu tiên lập lịch (Renice tiến trình)
   d / s      : Đổi tần suất làm mới (Refresh Delay, ví dụ 1.0 giây)
   Shift + W  : Lưu toàn bộ cấu hình hiện tại vào file ~/.toprc
   q          : Thoát khỏi top
=============================================================================
```

---

## 7. Lời kết

Lệnh `top` tựa như một con dao đa năng Thụy Sĩ thu nhỏ trong thế giới quản trị hệ thống Linux: nhỏ gọn, không phụ thuộc thư viện rườm rà, luôn hiện diện sẵn sàng và ẩn chứa sức mạnh phi thường.

Hiểu rõ bản chất từng chỉ số của `top` — phân biệt được **Load Average** với mức sử dụng CPU, phân biệt được **`wa` (Disk nghẽn)** với **`st` (Cloud nghẽn)**, phân biệt được **`VIRT`** với **`RES`** — chính là lằn ranh phân biệt giữa một người dùng Linux cơ bản và một kỹ sư SRE có tư duy chẩn đoán hệ thống sâu sắc.

Hy vọng bài viết này sẽ trở thành cẩm nang gối đầu giường giúp bạn tự tin làm chủ và thuần phục hoàn toàn lệnh `top` trong mọi tình huống thực chiến!
