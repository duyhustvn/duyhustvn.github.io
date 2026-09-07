#!/usr/bin/env python3
"""
TCP State Inspector & Lab (Kiem chung trang thai TCP va lenh ss)
Tac gia: DuyHust / Blog Engineering
Chuc nang:
- Mo phong va kiem chung thuc nghiem cac trang thai TCP tren Linux.
- Su dung Python socket POSIX chuan, khong can quyen root (sudo).
- Tu dong capture va phan tich ket qua tu lenh `ss` (Socket Statistics).
- Ho tro ca che do chay tu dong (All Labs) va che do tuong tac (Interactive).
"""

import socket
import subprocess
import threading
import time
import argparse
import sys
import os

# ANSI Color Codes
GREEN = "\033[92m"
YELLOW = "\033[93m"
BLUE = "\033[94m"
CYAN = "\033[96m"
RED = "\033[91m"
BOLD = "\033[1m"
RESET = "\033[0m"

PAUSE_ON_STEP = False


def print_banner(title: str):
    print("\n" + "=" * 75)
    print(f"{BOLD}{CYAN}>>> {title} <<<{RESET}")
    print("=" * 75)


def print_step(step_num: int, desc: str):
    print(f"\n{BOLD}{YELLOW}[Bước {step_num}]{RESET} {desc}")
    if PAUSE_ON_STEP:
        input(
            f"{YELLOW}  ⏸️  [Tạm dừng] Mở terminal khác để tự kiểm tra nếu muốn. Nhấn [Enter] để tiếp tục...{RESET}"
        )


def run_ss_command(filter_expr: str = "", extra_flags: str = "-tan", title: str = ""):
    """Thực thi lệnh ss và in kết quả có định dạng rõ ràng."""
    cmd = ["ss"] + extra_flags.split()
    if filter_expr:
        cmd.extend(filter_expr.split())

    cmd_str = " ".join(cmd)
    if title:
        print(f"\n{BOLD}{BLUE}🔍 [Lệnh ss]: {cmd_str} ({title}){RESET}")
    else:
        print(f"\n{BOLD}{BLUE}🔍 [Lệnh ss]: {cmd_str}{RESET}")

    try:
        res = subprocess.run(cmd, capture_output=True, text=True, check=True)
        lines = res.stdout.strip().split("\n")
        if not lines or lines == [""]:
            print(f"{RED}  (Không tìm thấy socket nào thỏa mãn bộ lọc){RESET}")
            return ""

        header = lines[0]
        print(f"{BOLD}{header}{RESET}")
        for line in lines[1:]:
            if "ESTAB" in line:
                print(f"{GREEN}{line}{RESET}")
            elif "LISTEN" in line:
                print(f"{CYAN}{line}{RESET}")
            elif "CLOSE-WAIT" in line:
                print(f"{RED}{line}{RESET}")
            elif "TIME-WAIT" in line:
                print(f"{YELLOW}{line}{RESET}")
            elif "FIN-WAIT" in line:
                print(f"{BLUE}{line}{RESET}")
            elif "SYN-SENT" in line:
                print(f"{YELLOW}{line}{RESET}")
            else:
                print(line)
        return res.stdout
    except subprocess.CalledProcessError as e:
        err = e.stderr.strip() if e.stderr else str(e)
        print(f"{RED}Lỗi khi chạy lệnh ss: {err}{RESET}")
        return ""
    except Exception as e:
        print(f"{RED}Lỗi khi chạy lệnh ss: {e}{RESET}")
        return ""


def lab1_handshake_and_listen_queue():
    print_banner("LAB 1: Khởi tạo LISTEN & Bí mật Recv-Q / Send-Q của Bắt tay 3 bước")
    print(
        "Mục tiêu: Kiểm chứng trạng thái LISTEN và giải mã Recv-Q / Send-Q khi Client kết nối"
    )

    # 1. Tạo Server socket
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0))
    port = srv.getsockname()[1]
    backlog = 5
    srv.listen(backlog)

    print_step(
        1, f"Server gọi socket(), bind(127.0.0.1:{port}), listen(backlog={backlog})"
    )
    run_ss_command(
        f"sport = :{port}", extra_flags="-tan", title="Trạng thái LISTEN ban đầu"
    )

    # 2. Tạo 3 Client kết nối tới Server, NHƯNG Server CHƯA gọi accept()
    print_step(
        2,
        "3 Client đồng thời gọi connect() tới Server, nhưng Server CỐ TÌNH CHƯA gọi accept()",
    )
    clients = []
    for i in range(3):
        c = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        c.connect(("127.0.0.1", port))
        clients.append(c)

    time.sleep(0.1)
    run_ss_command(
        f"sport = :{port} or dport = :{port}",
        extra_flags="-tan",
        title="Sau khi 3 Client bắt tay xong",
    )

    print(f"""
{BOLD}{GREEN}💡 [PHÁT HIỆN THỰC NGHIỆM - LISTEN SOCKET]:{RESET}
- Đối với socket ở trạng thái {BOLD}LISTEN{RESET}:
  + {BOLD}Send-Q = {backlog}{RESET}: Chính là tham số backlog tối đa truyền vào hàm listen().
  + {BOLD}Recv-Q = 3{RESET}: Số lượng kết nối đã hoàn tất bắt tay 3 bước đang nằm trong {BOLD}Accept Queue{RESET},
    chờ ứng dụng server gọi hàm accept()!
- Cả 3 kết nối client đều đã đạt {BOLD}ESTAB{RESET} từ góc nhìn của Client và Kernel!
""")

    # 3. Server gọi accept()
    print_step(3, "Server tiến hành gọi accept() nhận 1 kết nối")
    conn, addr = srv.accept()
    time.sleep(0.1)
    run_ss_command(
        f"sport = :{port}",
        extra_flags="-tan",
        title="Recv-Q của LISTEN giảm từ 3 xuống 2 (1 kết nối đã được lấy ra khỏi Accept Queue)",
    )
    run_ss_command(
        f"sport = :{port}",
        extra_flags="-tan state established",
        title="Lọc riêng các kết nối ESTABLISHED (cú pháp: state established sport = :port)",
    )

    print(f"""
{BOLD}{GREEN}💡 [PHÁT HIỆN THỰC NGHIỆM - SAU KHI ACCEPT]:{RESET}
- Khi Server gọi {BOLD}accept(){RESET}, 1 kết nối được lấy ra khỏi {BOLD}Accept Queue{RESET}.
- Quan sát thấy {BOLD}Recv-Q của socket LISTEN đã giảm từ 3 xuống 2{RESET}!
- Lưu ý về cú pháp lệnh ss: Từ khóa {BOLD}state established{RESET} phải đặt trước biểu thức cổng
  ({CYAN}ss -tan state established sport = :{port}{RESET}), chứ không dùng toán tử 'and state established'.
""")

    # Dọn dẹp
    time.sleep(1)
    conn.close()
    for c in clients:
        c.close()
    srv.close()
    print(f"{GREEN}✓ Lab 1 hoàn tất và dọn dẹp xong.{RESET}")


def lab2_established_and_data_queues():
    print_banner(
        "LAB 2: Trạng thái ESTABLISHED & Ý nghĩa Recv-Q / Send-Q khi truyền dữ liệu"
    )
    print(
        "Mục tiêu: Khám phá cách Recv-Q phản ánh dung lượng bộ đệm nhận (Receive Buffer) chưa đọc"
    )

    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0))
    port = srv.getsockname()[1]
    srv.listen(1)

    client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    client.connect(("127.0.0.1", port))
    conn, addr = srv.accept()

    print_step(1, "Kết nối đã ESTABLISHED giữa Client và Server")
    run_ss_command(
        f"sport = :{port} or dport = :{port}",
        extra_flags="-tan",
        title="ESTABLISHED rỗng",
    )

    print_step(
        2, "Server gửi 16,384 bytes (16KB) sang Client, nhưng Client CHƯA gọi recv()"
    )
    payload = b"X" * 16384
    conn.sendall(payload)
    time.sleep(0.1)

    run_ss_command(
        f"sport = :{port} or dport = :{port}",
        extra_flags="-tan",
        title="Dữ liệu nằm chờ trong Receive Buffer",
    )

    print(f"""
{BOLD}{GREEN}💡 [PHÁT HIỆN THỰC NGHIỆM - ESTABLISHED SOCKET]:{RESET}
- Đối với socket ở trạng thái {BOLD}ESTABLISHED{RESET}, ý nghĩa Recv-Q/Send-Q hoàn toàn thay đổi:
  + {BOLD}Recv-Q phía Client = 16384{RESET}: Số byte dữ liệu kernel đã nhận từ mạng nhưng ứng dụng
    Client chưa gọi hàm recv() / read() để lấy lên!
  + {BOLD}Send-Q phía Server = 0{RESET}: Toàn bộ 16KB đã được kernel Client gửi gói ACK xác nhận an toàn.
""")

    print_step(3, "Client gọi recv() để tiêu thụ toàn bộ dữ liệu trong bộ đệm")
    data = client.recv(16384)
    print(f"Client đã đọc thành công {len(data)} bytes.")
    time.sleep(0.1)
    run_ss_command(
        f"sport = :{port} or dport = :{port}",
        extra_flags="-tan",
        title="Sau khi Client đã đọc",
    )

    conn.close()
    client.close()
    srv.close()
    print(f"{GREEN}✓ Lab 2 hoàn tất và dọn dẹp xong.{RESET}")


def lab3_fin_wait_2_and_close_wait_leak():
    print_banner(
        "LAB 3: Chủ động đóng kết nối, TCP Half-Closed và Mô phỏng Rò rỉ CLOSE-WAIT"
    )
    print(
        "Mục tiêu: Kiểm chứng FIN-WAIT-2, CLOSE-WAIT và chứng minh tại sao CLOSE-WAIT không bao giờ tự mất"
    )

    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0))
    port = srv.getsockname()[1]
    srv.listen(5)

    client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    client.connect(("127.0.0.1", port))
    conn, addr = srv.accept()

    print_step(
        1,
        "Client là Bên chủ động đóng (Active Closer), gọi shutdown(SHUT_WR) để gửi gói FIN",
    )
    # Sử dụng shutdown(SHUT_WR) để socket client vẫn giữ FD còn mở chiều đọc
    client.shutdown(socket.SHUT_WR)
    time.sleep(0.1)

    print_step(2, "Quan sát trạng thái 2 đầu khi Server CHƯA gọi close():")
    run_ss_command(
        f"sport = :{port} or dport = :{port}",
        extra_flags="-tanp",
        title="FIN-WAIT-2 và CLOSE-WAIT",
    )

    print(f"""
{BOLD}{GREEN}💡 [PHÁT HIỆN THỰC NGHIỆM]:{RESET}
- {BOLD}Client chuyển sang FIN-WAIT-2{RESET}: Chiều gửi của Client đã đóng.
- {BOLD}Server chuyển sang CLOSE-WAIT{RESET}: Kernel của Server tự động phản hồi ACK ngay lập tức,
  nhưng ứng dụng Server CHƯA gọi close()!
- Chú ý: {BOLD}Recv-Q phía Server = 1{RESET}: Dấu hiệu báo EOF (End-of-File) trên socket.
""")

    print_step(
        3,
        "Kiểm chứng tính chất Half-Closed: Server vẫn có thể GỬI DỮ LIỆU sang Client!",
    )
    conn.sendall(b"Server: Toi van con du lieu muon gui cho ban truoc khi dong!\n")
    received_in_fin_wait_2 = client.recv(1024)
    print(
        f"Client nhận được tin nhắn khi đang ở FIN-WAIT-2:\n  -> {YELLOW}{received_in_fin_wait_2.decode().strip()}{RESET}"
    )

    print_step(4, "Mô phỏng BUG rò rỉ Socket (Socket Leak) trên Production:")
    print(
        "Mở thêm 3 kết nối client khác và client đóng ngay, trong khi server 'quên' gọi close()..."
    )
    leaked_clients = []
    leaked_conns = []
    for _ in range(3):
        c = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        c.connect(("127.0.0.1", port))
        cn, _ = srv.accept()
        c.close()  # Client đóng hoàn toàn
        leaked_conns.append(cn)  # Server giữ lại kết nối, không đóng!

    time.sleep(0.2)
    run_ss_command(
        f"sport = :{port}",
        extra_flags="-tanp state close-wait",
        title="Tích tụ các socket kẹt CLOSE-WAIT",
    )

    print(f"""
{BOLD}{RED}⚠️ [CẢNH BÁO PRODUCTION - RÒ RỈ CLOSE-WAIT]:{RESET}
Các socket CLOSE-WAIT này sẽ {BOLD}TỒN TẠI VĨNH VIỄN{RESET} chừng nào tiến trình còn chạy, vì
Linux Kernel KHÔNG BAO GIỜ tự ý đóng kết nối của ứng dụng!
Chỉ khi code ứng dụng gọi close() thì socket mới được giải phóng.
""")

    print_step(
        5,
        "Giải cứu Server: Ứng dụng Server duyệt qua và gọi close() cho các socket bị bỏ quên",
    )
    conn.close()
    for cn in leaked_conns:
        cn.close()
    client.close()
    srv.close()
    time.sleep(0.1)
    run_ss_command(
        f"sport = :{port}",
        extra_flags="-tan",
        title="Toàn bộ CLOSE-WAIT đã được giải phóng",
    )
    print(f"{GREEN}✓ Lab 3 hoàn tất.{RESET}")


def lab4_time_wait_countdown_timer():
    print_banner("LAB 4: Khám phá TIME-WAIT và Bộ đếm 2MSL (60s Countdown Timer)")
    print(
        "Mục tiêu: Quan sát trạng thái TIME-WAIT và theo dõi bộ đếm thời gian thực bằng cờ `-o` trong ss"
    )

    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0))
    port = srv.getsockname()[1]
    srv.listen(1)

    client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    client.connect(("127.0.0.1", port))
    conn, addr = srv.accept()

    print_step(1, "Client chủ động đóng kết nối (Active Closer gọi close)")
    client.close()
    time.sleep(0.05)

    print_step(2, "Server nhận được EOF và đóng nốt (Passive Closer gọi close)")
    conn.close()
    time.sleep(0.05)

    print_step(
        3, "Quan sát TIME-WAIT trên Client bằng cờ `ss -tan -o` (Hiển thị Timer):"
    )
    run_ss_command(
        f"sport = :{port} or dport = :{port}",
        extra_flags="-tan -o",
        title="Thời điểm bắt đầu TIME-WAIT",
    )

    print("\nĐang đếm ngược 3 giây để kiểm chứng timer thực tế...")
    for remaining in range(3, 0, -1):
        print(f"  ... đợi {remaining}s")
        time.sleep(1)

    run_ss_command(
        f"sport = :{port} or dport = :{port}",
        extra_flags="-tan -o",
        title="Timer sau 3 giây",
    )

    print(f"""
{BOLD}{GREEN}💡 [PHÁT HIỆN THỰC NGHIỆM - TIME-WAIT TIMER]:{RESET}
- Cờ {BOLD}-o (options/timers){RESET} của lệnh ss cung cấp thông tin cực kỳ quý giá:
  Ví dụ: {YELLOW}timer:(timewait,56sec,0){RESET}
- Con số này đếm lùi từ 60 giây (2 * MSL) về 0 trước khi socket hoàn toàn biến mất (CLOSED).
- Phía Passive Closer (Server) lập tức CLOSED và biến mất khỏi bảng ss, KHÔNG HỀ bị TIME-WAIT!
""")

    srv.close()
    print(f"{GREEN}✓ Lab 4 hoàn tất.{RESET}")


def lab5_server_as_active_closer():
    print_banner(
        "LAB 5: Khi nào Server bị TIME-WAIT? (Xóa bỏ hiểu lầm 'Chỉ Client bị TIME-WAIT')"
    )
    print(
        "Mục tiêu: Chứng minh nếu Server chủ động ngắt kết nối trước (Keep-Alive Timeout), Server sẽ dính TIME-WAIT"
    )

    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0))
    port = srv.getsockname()[1]
    srv.listen(1)

    client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    client.connect(("127.0.0.1", port))
    conn, addr = srv.accept()

    print_step(
        1,
        "Server là bên CHỦ ĐỘNG ĐÓNG TRƯỚC (Ví dụ: Nginx ngắt kết nối do Client idle quá lâu)",
    )
    conn.close()
    time.sleep(0.05)

    print_step(2, "Client nhận tín hiệu EOF và đóng socket phía mình")
    client.close()
    time.sleep(0.05)

    print_step(3, f"Kiểm tra cổng của Server ({port}) và cổng ngẫu nhiên của Client:")
    run_ss_command(
        f"sport = :{port} or dport = :{port}",
        extra_flags="-tan -o",
        title="Server bị TIME-WAIT trên chính port của mình",
    )

    print(f"""
{BOLD}{GREEN}💡 [PHÁT HIỆN THỰC NGHIỆM - SERVER TRỞ THÀNH ACTIVE CLOSER]:{RESET}
- Hãy nhìn vào cột {BOLD}Local Address:Port{RESET}: Cổng {BOLD}127.0.0.1:{port}{RESET} (Server) đang giữ trạng thái {YELLOW}TIME-WAIT{RESET}!
- Điều này chứng minh định lý căn bản: {BOLD}Bên nào gọi close() trước, bên đó sẽ phải gánh chịu TIME-WAIT!{RESET}
- Đây là lý do tại sao các Reverse Proxy (như Nginx, Envoy) hoặc API Gateway bị cạn kiệt port nếu cấu hình
  timeout chủ động đóng kết nối client quá ngắn mà không dùng Connection Pooling.
""")

    srv.close()
    print(f"{GREEN}✓ Lab 5 hoàn tất.{RESET}")


def lab6_syn_sent_simulation():
    print_banner(
        "LAB 6: Mô phỏng kẹt SYN-SENT (Khi gói tin bị DROP bởi Firewall hoặc mạng lỗi)"
    )
    print(
        "Mục tiêu: Tạo kết nối không phản hồi và quan sát trạng thái SYN-SENT cùng Send-Q"
    )

    # Sử dụng dải RFC 5737 TEST-NET-1 (192.0.2.1) - dải IP được quy chuẩn không bao giờ phản hồi gói tin
    blackhole_ip = "192.0.2.1"
    blackhole_port = 80

    print_step(
        1,
        f"Client gọi connect() non-blocking tới địa chỉ hố đen {blackhole_ip}:{blackhole_port}",
    )
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setblocking(False)
    try:
        s.connect((blackhole_ip, blackhole_port))
    except BlockingIOError:
        pass  # Kết nối đang chờ xử lý trong background

    time.sleep(0.2)
    print_step(2, "Chạy lệnh ss lọc theo đích đến (dst):")
    run_ss_command(
        f"dst {blackhole_ip}", extra_flags="-tan", title="Trạng thái SYN-SENT"
    )

    print(f"""
{BOLD}{GREEN}💡 [PHÁT HIỆN THỰC NGHIỆM - SYN-SENT]:{RESET}
- Trạng thái hiển thị là {YELLOW}SYN-SENT{RESET}.
- Hãy nhìn vào cột {BOLD}Send-Q = 1{RESET}: Đại diện cho gói tin SYN đã gửi đi nhưng chưa nhận được ACK/SYN-ACK!
- Nếu trên hệ thống có nhiều socket kẹt ở SYN-SENT, đây là dấu hiệu 100% của việc {RED}Firewall DROP gói tin{RESET},
  hoặc cấu hình routing bị sai, chứ KHÔNG PHẢI port bị đóng (nếu port đóng, server sẽ trả lời RST ngay lập tức).
""")

    s.close()
    print(f"{GREEN}✓ Lab 6 hoàn tất.{RESET}")


def interactive_menu():
    labs = [
        (
            "Lab 1: Khởi tạo LISTEN & Bí mật Recv-Q/Send-Q của Bắt tay 3 bước",
            lab1_handshake_and_listen_queue,
        ),
        (
            "Lab 2: ESTABLISHED & Cơ chế đệm dữ liệu (Recv-Q khi chưa đọc)",
            lab2_established_and_data_queues,
        ),
        (
            "Lab 3: Active Half-Close (FIN-WAIT-2) & Mô phỏng rò rỉ CLOSE-WAIT",
            lab3_fin_wait_2_and_close_wait_leak,
        ),
        (
            "Lab 4: Trạng thái TIME-WAIT & Bộ đếm thời gian 2MSL (-o timer)",
            lab4_time_wait_countdown_timer,
        ),
        (
            "Lab 5: Server làm Bên chủ động đóng (Ai sẽ chịu TIME-WAIT?)",
            lab5_server_as_active_closer,
        ),
        (
            "Lab 6: Mô phỏng kẹt SYN-SENT (Tắc nghẽn mạng / Firewall Drop)",
            lab6_syn_sent_simulation,
        ),
    ]

    while True:
        print("\n" + "=" * 65)
        print(
            f"{BOLD}{CYAN}=== HỆ THỐNG THỰC NGHIỆM KIỂM CHỨNG TRẠNG THÁI TCP & SS ==={RESET}"
        )
        print("=" * 65)
        for idx, (name, _) in enumerate(labs, 1):
            print(f" [{idx}] {name}")
        print(" [A] Chạy toàn bộ tất cả các Lab (Full Suite)")
        print(" [0] Thoát chương trình")
        print("-" * 65)

        choice = (
            input(f"{BOLD}Nhập lựa chọn của bạn (0-6 hoặc A): {RESET}").strip().upper()
        )

        if choice == "0":
            print("Tạm biệt!")
            break
        elif choice == "A":
            for _, lab_fn in labs:
                lab_fn()
                time.sleep(1)
        elif choice.isdigit() and 1 <= int(choice) <= len(labs):
            labs[int(choice) - 1][1]()
        else:
            print(f"{RED}Lựa chọn không hợp lệ, vui lòng chọn lại!{RESET}")


def main():
    global PAUSE_ON_STEP
    parser = argparse.ArgumentParser(
        description="Chương trình thực nghiệm kiểm chứng 11 trạng thái TCP và lệnh ss"
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Tự động chạy toàn bộ các kịch bản thực nghiệm",
    )
    parser.add_argument(
        "--lab",
        type=int,
        choices=range(1, 7),
        help="Chạy một kịch bản Lab cụ thể (1-6)",
    )
    parser.add_argument(
        "-p",
        "--pause",
        action="store_true",
        help="Tạm dừng sau mỗi bước để mở terminal khác kiểm tra",
    )
    args = parser.parse_args()

    if args.pause:
        PAUSE_ON_STEP = True

    labs = {
        1: lab1_handshake_and_listen_queue,
        2: lab2_established_and_data_queues,
        3: lab3_fin_wait_2_and_close_wait_leak,
        4: lab4_time_wait_countdown_timer,
        5: lab5_server_as_active_closer,
        6: lab6_syn_sent_simulation,
    }

    if args.all:
        for i in range(1, 7):
            labs[i]()
            time.sleep(0.5)
    elif args.lab:
        labs[args.lab]()
    else:
        interactive_menu()


if __name__ == "__main__":
    main()
