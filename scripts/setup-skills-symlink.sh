#!/usr/bin/env bash
# =============================================================================
# setup-skills-symlink.sh
# Di chuyển skills/trade-agent/ vào trong git repo trade-agent/
# rồi tạo symlink ngược để ZeroClaw vẫn đọc được ở vị trí cũ.
#
# Cách chạy:
#   cd /Users/maianhnguyen/.zeroclaw/workspace
#   bash setup-skills-symlink.sh
# =============================================================================
set -euo pipefail

WORKSPACE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKILL_SRC="$WORKSPACE_DIR/skills/trade-agent"
GIT_REPO="$WORKSPACE_DIR/trade-agent"
SKILL_DST="$GIT_REPO/skills/trade-agent"
SYMLINK="$WORKSPACE_DIR/skills/trade-agent"

echo "================================================="
echo " trade-agent skills symlink setup"
echo "================================================="
echo "Workspace : $WORKSPACE_DIR"
echo "Git repo  : $GIT_REPO"
echo "Skill src : $SKILL_SRC"
echo "Skill dst : $SKILL_DST"
echo "Symlink   : $SYMLINK → ../trade-agent/skills/trade-agent"
echo ""

# ── Kiểm tra tiền điều kiện ──────────────────────────────────────────────────
if [[ ! -d "$GIT_REPO/.git" ]]; then
  echo "❌ Không tìm thấy git repo tại: $GIT_REPO"
  exit 1
fi

if [[ ! -d "$SKILL_SRC" ]]; then
  echo "❌ Không tìm thấy skill folder tại: $SKILL_SRC"
  exit 1
fi

if [[ -L "$SYMLINK" ]]; then
  echo "⚠️  Symlink đã tồn tại: $SYMLINK"
  echo "   Bỏ qua bước tạo symlink (đã setup rồi)."
  echo "   Kiểm tra: ls -la $WORKSPACE_DIR/skills/"
  exit 0
fi

# ── Bước 1: Copy skill vào git repo ──────────────────────────────────────────
echo "📁 [1/4] Tạo thư mục đích trong git repo..."
mkdir -p "$SKILL_DST"

echo "📋 [2/4] Copy skill files vào git repo..."
cp -r "$SKILL_SRC/." "$SKILL_DST/"
echo "   ✅ Đã copy: SKILL.md, wrapper.sh, reports/"

# ── Bước 2: Xóa folder gốc, thay bằng symlink ────────────────────────────────
echo "🗑️  [3/4] Xóa folder gốc (sắp thay bằng symlink)..."
rm -rf "$SKILL_SRC"

echo "🔗 [4/4] Tạo symlink tương đối..."
# Dùng path tương đối để symlink hoạt động khi di chuyển workspace
cd "$WORKSPACE_DIR/skills"
ln -s "../trade-agent/skills/trade-agent" "trade-agent"
cd "$WORKSPACE_DIR"

# ── Xác nhận ─────────────────────────────────────────────────────────────────
echo ""
echo "================================================="
echo " ✅ Hoàn thành! Kết quả:"
echo "================================================="
echo ""
echo "📂 workspace/skills/"
ls -la "$WORKSPACE_DIR/skills/"
echo ""
echo "📂 Nội dung qua symlink:"
ls "$SYMLINK/"
echo ""
echo "📌 Tiếp theo:"
echo "   cd $GIT_REPO"
echo "   git add skills/"
echo "   git commit -m 'feat: add ZeroClaw skill (wrapper + SKILL.md)'"
echo ""
echo "   Thêm vào .gitignore để không commit report output:"
echo "   echo 'skills/trade-agent/reports/latest.md' >> $GIT_REPO/.gitignore"
