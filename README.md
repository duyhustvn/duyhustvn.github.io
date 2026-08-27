# Hướng dẫn cài đặt và sử dụng Hugo với theme Blowfish

Tài liệu hướng dẫn thiết lập blog cá nhân sử dụng **Hugo** và theme **Blowfish**, bao gồm cách cấu hình và tạo bài viết mới.

---

## 1. Cài đặt Hugo (Extended)

Yêu cầu phiên bản Hugo Extended (hỗ trợ SCSS/SASS):

```bash
CGO_ENABLED=1 go install -tags extended,withdeploy github.com/gohugoio/hugo@latest
```

Kiểm tra cài đặt:
```bash
hugo version
```

---

## 2. Cài đặt Theme Blowfish

Thêm Blowfish vào dự án dưới dạng git submodule:

```bash
git submodule add -b main https://github.com/nunocoracao/blowfish.git themes/blowfish
```

---

## 3. Cấu hình Theme Blowfish

Theme Blowfish khuyến nghị sử dụng cấu trúc thư mục `config/_default/` để quản lý cấu hình chi tiết:

### Bước 3.1: Sao chép file cấu hình mẫu từ theme
```bash
mkdir -p config/_default
cp -r themes/blowfish/config/_default/* config/_default/
```

### Bước 3.2: Xóa file `hugo.toml` ở thư mục gốc (nếu có)
Tránh xung đột cấu hình giữa thư mục gốc và `config/_default/`:
```bash
rm hugo.toml
```

> **Lưu ý:** Bạn có thể chỉnh sửa thông tin blog (tên tác giả, logo, menu, mạng xã hội,...) tại các file trong thư mục `config/_default/`:
> - `hugo.toml`: Cấu hình chung và baseURL
> - `params.toml`: Cấu hình giao diện, tính năng của Blowfish
> - `menus.en.toml` (hoặc `menus.vi.toml`): Thanh điều hướng (menu)
> - `languages.en.toml` (hoặc `languages.vi.toml`): Ngôn ngữ và thông tin tác giả

---

### Bước 3.3: Cấu hình Tiếng Việt cho Blog (Tùy chọn)

Theme Blowfish đã **hỗ trợ sẵn bản dịch Tiếng Việt** (`themes/blowfish/i18n/vi.yaml`). Để chuyển blog sang Tiếng Việt hoàn toàn:

1. Trong file `config/_default/hugo.toml`, đổi ngôn ngữ mặc định:
   ```toml
   defaultContentLanguage = "vi"
   pluralizeListTitles = false   # Tắt tự động thêm "s" vào tên danh mục tiếng Việt
   ```

2. Đổi tên file `languages.en.toml` thành `languages.vi.toml`:
   ```bash
   mv config/_default/languages.en.toml config/_default/languages.vi.toml
   ```
   Sau đó mở `config/_default/languages.vi.toml` và cập nhật:
   ```toml
   locale = "vi"
   label = "Tiếng Việt"
   title = "Tên Blog Của Bạn"

   [params]
     displayName = "VI"
     isoCode = "vi"
     dateFormat = "02/01/2006" # Định dạng Ngày/Tháng/Năm
     description = "Mô tả ngắn về blog của bạn"
     copyright = "Bản quyền © 2026 Tên Tác Giả"

   [params.author]
     name = "Tên Tác Giả"
     headline = "Lập trình viên / Blogger"
     bio = "Mô tả ngắn về bản thân bạn"
   ```

3. Đổi tên file `menus.en.toml` thành `menus.vi.toml`:
   ```bash
   mv config/_default/menus.en.toml config/_default/menus.vi.toml
   ```
   Cập nhật menu tiếng Việt trong `config/_default/menus.vi.toml`:
   ```toml
   [[main]]
     name = "Bài viết"
     pageRef = "posts"
     weight = 10

   [[main]]
     name = "Chuyên mục"
     pageRef = "categories"
     weight = 20

   [[main]]
     name = "Thẻ"
     pageRef = "tags"
     weight = 30
   ```

---

## 4. Hướng dẫn tạo bài viết mới

Có 2 cách để tạo bài viết tùy thuộc vào nhu cầu quản lý tài nguyên (hình ảnh):

### Cách 1: Tạo dạng Page Bundle (Khuyên dùng)
Phù hợp với bài viết có kèm hình ảnh hoặc tài liệu đính kèm.

```bash
hugo new posts/ten-bai-viet/index.md
```

Cấu trúc thư mục tạo ra:
```text
content/
└── posts/
    └── ten-bai-viet/
        ├── index.md        # Nội dung bài viết
        └── feature.jpg     # Ảnh đại diện (tùy chọn)
```

### Cách 2: Tạo dạng file Markdown đơn
Phù hợp với bài viết ngắn, thuần văn bản:

```bash
hugo new posts/ten-bai-viet.md
```

---

## 5. Cấu trúc Front Matter mẫu cho bài viết

Mở file Markdown vừa tạo (`index.md` hoặc `ten-bai-viet.md`), chỉnh sửa phần thông tin ở đầu trang (Front Matter):

```yaml
---
title: "Tiêu đề bài viết của bạn"
date: 2026-08-27T12:00:00+07:00
draft: false
description: "Mô tả ngắn gọn về bài viết để hiển thị bản xem trước và hỗ trợ SEO"
summary: "Tóm tắt bài viết xuất hiện trên trang chủ"
tags: ["Hugo", "Blowfish", "HuongDan"]
categories: ["LapTrinh", "Blog"]
series: ["Series Hugo"]
series_order: 1
showTableOfContents: true
---

## Giới thiệu

Nội dung bài viết bắt đầu từ đây. Bạn có thể sử dụng cú pháp **Markdown** thông thường.

### Chèn hình ảnh

Nếu dùng Page Bundle (Cách 1), bạn chỉ cần để ảnh cùng thư mục với file `index.md` và chèn vào bài viết:

![Ảnh minh họa](feature.jpg)

### Chèn mã nguồn (Code Block)

```python
def hello_world():
    print("Xin chào từ Hugo & Blowfish!")
```
```

### Giải thích các trường Front Matter:
- `title`: Tiêu đề hiển thị của bài viết.
- `date`: Thời gian đăng bài.
- `draft`: Đặt `true` nếu là bài nháp (sẽ không xuất hiện khi build trang chính thức), đổi thành `false` khi muốn xuất bản.
- `description` / `summary`: Đoạn mô tả vắn tắt bài viết.
- `tags` & `categories`: Phân loại bài viết theo thẻ và danh mục.
- `showTableOfContents`: Đặt `true` để tự động tạo mục lục bên cạnh bài viết.

---

## 6. Chạy Local Server (Xem trước bài viết)

Khởi động server cục bộ để xem trước blog trên trình duyệt:

```bash
hugo server -D
```

- Cờ `-D` (hoặc `--buildDrafts`): Hiển thị cả các bài viết đang ở trạng thái nháp (`draft: true`).
- Truy cập blog tại: `http://localhost:1313/`
- Trang web sẽ tự động tải lại (Live Reload) mỗi khi bạn lưu thay đổi trong file.

---

## 7. Build trang web (Thủ công)

```bash
hugo --minify
```

Toàn bộ trang web tĩnh sẽ được tạo trong thư mục `public/`.

---

## 8. Hướng dẫn Deploy lên GitHub Pages (Tự động)

Dự án đã được thiết lập sẵn quy trình **GitHub Actions** tự động build và deploy tại [`.github/workflows/deploy.yml`](.github/workflows/deploy.yml).

### Các bước thực hiện:

1. **Đẩy mã nguồn lên GitHub:**
   ```bash
   git add .
   git commit -m "Thiết lập blog Hugo Blowfish và GitHub Actions"
   git remote add origin https://github.com/<tai-khoan-cua-ban>/<ten-repo>.git
   git branch -M main
   git push -u origin main
   ```

2. **Kích hoạt GitHub Pages trên GitHub:**
   - Truy cập vào Repository trên GitHub của bạn.
   - Chọn **Settings** (Cài đặt) -> mục **Pages** (ở thanh bên trái).
   - Tại mục **Build and deployment** > **Source**, chọn **`GitHub Actions`**.

3. **Xem thành quả:**
   - Chuyển sang tab **Actions** trên repository để theo dõi tiến trình deploy tự động.
   - Khi hoàn tất, blog của bạn sẽ truy cập được tại địa chỉ:
     `https://<tai-khoan-cua-ban>.github.io/<ten-repo>/` (hoặc `https://<tai-khoan-cua-ban>.github.io/` nếu tên repo trùng với tên tài khoản).
   - Mỗi lần bạn viết bài mới và `git push` lên nhánh `main`, GitHub Actions sẽ tự động cập nhật bài viết lên trang web.

