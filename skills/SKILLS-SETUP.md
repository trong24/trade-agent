# Skills — Cấu trúc & Đồng bộ

## Tổng quan

Thư mục `skills/` trong repo này chứa các **ZeroClaw skill** liên quan đến
`trade-agent`. Skills được **git-tracked** tại đây và được expose ra ngoài
workspace thông qua một **symlink**, để ZeroClaw đọc được ở đúng vị trí cần.

---

## Cấu trúc thực tế

```
~/.zeroclaw/workspace/
│
├── trade-agent/                  ← Git repo (bạn đang đọc file này)
│   ├── .git/
│   ├── src/
│   ├── data/
│   └── skills/                   ← Chứa skills, ĐƯỢC TRACK bởi git ✅
│       ├── SKILLS-SETUP.md       ← File này
│       └── trade-agent/
│           ├── SKILL.md          ← Mô tả skill cho ZeroClaw
│           ├── wrapper.sh        ← Runner script
│           └── reports/
│               ├── report-template.md
│               └── latest.md     ← ⚠️ gitignored (generated output)
│
└── skills/                       ← ZeroClaw đọc từ đây
    └── trade-agent  →  ../trade-agent/skills/trade-agent   ← SYMLINK 🔗
```

---

## Cách hoạt động

```
ZeroClaw đọc:    workspace/skills/trade-agent/SKILL.md
                         ↓ (symlink)
Thực ra là:      workspace/trade-agent/skills/trade-agent/SKILL.md
                         ↓ (git-tracked)
Git commit:      trade-agent repo → skills/trade-agent/
```

**Khi bạn sửa `wrapper.sh` hoặc `SKILL.md`:**  
→ Chỉ cần `git add skills/ && git commit` trong `trade-agent/`  
→ ZeroClaw tự động thấy bản mới qua symlink (không cần copy thủ công)

---

## Setup lần đầu (clone repo mới)

Sau khi `git clone trade-agent`, symlink **chưa tồn tại**. Chạy:

```bash
cd ~/.zeroclaw/workspace
bash setup-skills-symlink.sh
```

Hoặc tự tạo symlink thủ công:

```bash
cd ~/.zeroclaw/workspace/skills
ln -s "../trade-agent/skills/trade-agent" trade-agent
```

> ⚠️ **Lưu ý:** Symlink nằm ở `workspace/skills/` — **ngoài** git repo.  
> Git không track symlink này. Mỗi máy mới cần chạy lại bước trên 1 lần.

---

## .gitignore

File `trade-agent/.gitignore` nên có:

```gitignore
# Skills — generated output (không commit report chạy thực tế)
skills/trade-agent/reports/latest.md
```

Template (`report-template.md`) vẫn được commit bình thường.

---

## Workflow hàng ngày

```bash
# Sửa skill
vim ~/.zeroclaw/workspace/trade-agent/skills/trade-agent/wrapper.sh

# Commit vào git
cd ~/.zeroclaw/workspace/trade-agent
git add skills/
git commit -m "fix(skill): cập nhật wrapper để hỗ trợ --stop-loss"
git push

# ZeroClaw tự động dùng bản mới (qua symlink)
```

---

## Kiểm tra symlink còn hoạt động không

```bash
ls -la ~/.zeroclaw/workspace/skills/
# Phải thấy:  trade-agent -> ../trade-agent/skills/trade-agent

ls ~/.zeroclaw/workspace/skills/trade-agent/
# Phải thấy:  SKILL.md  wrapper.sh  reports/
```

---

*Setup by: Antigravity — 2026-02-25*
