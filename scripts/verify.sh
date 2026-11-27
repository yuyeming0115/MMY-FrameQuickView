#!/usr/bin/env bash
# =====================================================================
# MMY-FrameQuickView 一键验证脚本
#
# 作用：把「跑一堆测试 + 逐帧画面比对」合并成一条命令，跑完只告诉你
#       「全部通过」，或者「哪几项失败了、失败原因是什么」。
#
# 用法（在项目根目录打开 Git Bash，输入下面任意一条）：
#   bash scripts/verify.sh            # 快速：关键测试（约 1~2 分钟）  ← 平时用这个
#   bash scripts/verify.sh golden     # 只做逐帧画面标准答案比对（约 2 分钟）
#   bash scripts/verify.sh all        # tests/ 下所有测试（约 4~6 分钟）
#   bash scripts/verify.sh full       # 所有测试 + 标准答案比对（改了渲染/叠层代码后用）
#
# ⚠ 运行期间请不要按 Ctrl+C（在 Git Bash 里 Ctrl+C = 停止程序，不是复制）。
#   终端没有新输出时是在跑耗时的测试，等它自己结束。跑完再选中文字复制。
#
# 每个测试的完整输出保存在 LOG_DIR 里（见下面），失败时脚本会自动显示最后几行。
# =====================================================================

# set -u：用到没定义过的变量时直接报错，防止手误。
# 注意不用 set -e：我们希望某个测试失败后继续跑下一个，最后一起汇报。
set -u

# ---------------------------------------------------------------------
# 1. 准备工作
# ---------------------------------------------------------------------
# 脚本放在 scripts/ 里，所以「上一级」就是项目根目录。先切过去，后面的相对路径才对。
cd "$(dirname "$0")/.." || { echo "❌ 无法进入项目根目录"; exit 2; }

PY=".venv/Scripts/python.exe"          # 项目自己的 Python（系统自带的已损坏，见 README）
if [ ! -f "$PY" ]; then
  echo "❌ 找不到 $PY，请先按 README 建好 .venv"
  exit 2
fi

export PYTHONUTF8=1                    # 输出用 UTF-8：否则带 ✓ ✅ 的测试在写日志时会因编码报错
export QT_QPA_PLATFORM=offscreen       # 界面测试不弹出真实窗口

MODE="${1:-quick}"                     # 没传参数就用 quick
case "$MODE" in
  quick|golden|all|full) ;;
  *) echo "用法：bash scripts/verify.sh [quick|golden|all|full]"; exit 2 ;;
esac

# 日志目录：放系统临时目录里，不在项目里，不会被 git 提交
LOG_DIR="${TMPDIR:-/tmp}/fqv_verify"
mkdir -p "$LOG_DIR"

# 按了 Ctrl+C 时给个明确提示（避免把「被中断」误当成「测试失败」）
trap 'echo; echo "⚠ 你按了 Ctrl+C，验证被中断，结果不完整，请重新运行"; exit 130' INT

# ---------------------------------------------------------------------
# 2. 要跑哪些测试
# ---------------------------------------------------------------------
# 快速模式：这几轮改动涉及的关键测试（叠层、渲染、导出、显示层、布局…）
QUICK_TESTS=(
  tests/test_m45_batch.py
  tests/test_m45_export.py
  tests/test_m45_compose_render.py
  tests/test_m44_quick_fixes.py
  tests/test_m42_auto_weapon_and_look_split.py
  tests/test_m38_wear_look.py
  tests/test_m37_look_combine.py
  tests/test_m36_worldboss_blank.py
  tests/test_m34_canvas_align.py
  tests/test_m28_wing_library.py
  tests/test_m26_fx_library.py
  tests/test_m25_fx_layer.py
  tests/test_m23_outfit_group.py
  tests/test_layertoggle_part_visibility.py
  tests/test_m8_layout.py
  tests/test_m35_b_toolbar_overlay.py
  tests/test_m32_hud_gui.py
  tests/test_m39_ui_defaults.py
  tests/smoke_gui.py
)

# 全部模式：tests/ 下所有 test_*.py 和 smoke_*.py（星号会自动展开成文件列表）
ALL_TESTS=(tests/test_*.py tests/smoke_*.py)

# 已知「遗留失败」：改动之前就失败的测试（见 .workbuddy-ai/memory/MEMORY.md），
# 它们失败时显示「已知遗留」，不算这次的失败。修好它们之后请把名字从这里删掉。
KNOWN_FAIL=(
  tests/test_m21_e2e_realdir.py
  tests/test_m40_atlas_and_copy_frame.py
)

# ---------------------------------------------------------------------
# 3. 小工具
# ---------------------------------------------------------------------
TOTAL=0; PASSED=0; KNOWN=0
FAILED=()            # 失败的测试文件名
GOLDEN_FAIL=0        # 标准答案比对是否失败（0=没失败）
GOLDEN_RAN=0

# 判断某个测试是不是「已知遗留失败」：是就返回 0（成功），不是返回 1
is_known() {
  local x
  for x in "${KNOWN_FAIL[@]}"; do
    [ "$x" = "$1" ] && return 0
  done
  return 1
}

# 显示日志时去掉无关的 Qt 提示（这些在「无窗口模式」下是正常的，不是错误）
clean() {
  grep -v -e QFileSystemWatcher -e QFontDatabase -e "no longer ships" -e propagateSizeHints "$1"
}

# 依次运行传进来的所有测试，每个只打印一行结果
run_tests() {
  local f name log t0 code dt
  for f in "$@"; do
    if [ ! -f "$f" ]; then
      echo "  ⚠ 找不到 $f，跳过"
      continue
    fi
    name="$(basename "$f" .py)"
    log="$LOG_DIR/$name.log"
    t0=$SECONDS                          # $SECONDS 是 bash 自带的「脚本已运行秒数」
    "$PY" "$f" > "$log" 2>&1             # 全部输出写进日志文件，屏幕上不显示
    code=$?                              # 测试的退出码：0 = 通过
    dt=$((SECONDS - t0))
    TOTAL=$((TOTAL + 1))
    if [ "$code" -eq 0 ]; then
      PASSED=$((PASSED + 1))
      printf "  ✅ %-50s %3ss\n" "$name" "$dt"
    elif is_known "$f"; then
      KNOWN=$((KNOWN + 1))
      printf "  ➖ %-50s %3ss  已知遗留失败\n" "$name" "$dt"
    else
      FAILED+=("$f")
      printf "  ❌ %-50s %3ss  exit=%s\n" "$name" "$dt" "$code"
    fi
  done
}

# 逐帧画面「标准答案」比对（scripts/golden_frames.py check）
run_golden() {
  local log="$LOG_DIR/golden_check.log" t0=$SECONDS code line dt
  GOLDEN_RAN=1
  if [ ! -f tests/golden/m45_frames.json ]; then
    echo "  ❌ 找不到标准答案文件 tests/golden/m45_frames.json（需要先运行 golden_frames.py record）"
    GOLDEN_FAIL=1
    return
  fi
  echo "  ⏳ 逐帧画面比对中（约 2 分钟，期间没有输出是正常的）…"
  "$PY" scripts/golden_frames.py check > "$log" 2>&1
  code=$?
  dt=$((SECONDS - t0))
  line="$(grep '比对' "$log" | tail -1)"       # 例：比对 308 个组合：308 个一致，0 个不同
  if [ "$code" -eq 0 ]; then
    printf "  ✅ 逐帧画面标准答案  %s  %ss\n" "$line" "$dt"
    # 资源在录制后变了（美术改名/重导/删除）：不算失败，但列出来提醒一下
    if grep -q '资源已变 ' "$log"; then
      grep '⚠ 资源已变 ' "$log" | head -10
      echo "  （资源变化不算失败。资源稳定后建议重新录制：$PY scripts/golden_frames.py record --root ...）"
    fi
    # 旧格式标准答案 / 匹配表、模板被改过 等提示
    grep '  ℹ ' "$log" | head -5
  else
    GOLDEN_FAIL=1
    printf "  ❌ 逐帧画面标准答案  %s  %ss  exit=%s\n" "${line:-（没有跑完，见下面的报错）}" "$dt" "$code"
    if grep -q '✗' "$log"; then
      grep '✗' "$log" | head -10             # 有差异：列出前 10 条（会写明是「配方不同」还是「画面不同」）
    else
      clean "$log" | tail -12                # 没跑完：显示最后 12 行报错
    fi
  fi
}

# ---------------------------------------------------------------------
# 4. 开始跑
# ---------------------------------------------------------------------
START=$SECONDS
echo "▶ 验证模式：$MODE    日志目录：$LOG_DIR"
echo

case "$MODE" in
  quick)  echo "【关键测试】";   run_tests "${QUICK_TESTS[@]}" ;;
  all)    echo "【全部测试】";   run_tests "${ALL_TESTS[@]}" ;;
  golden) echo "【画面比对】";   run_golden ;;
  full)   echo "【全部测试】";   run_tests "${ALL_TESTS[@]}"
          echo; echo "【画面比对】"; run_golden ;;
esac

# ---------------------------------------------------------------------
# 5. 汇总（把这一段贴给我就行）
# ---------------------------------------------------------------------
echo
echo "================ 验证结果 ================"
echo "模式：$MODE    用时：$((SECONDS - START)) 秒"
if [ "$MODE" != "golden" ]; then
  echo "测试：共 $TOTAL 个，通过 $PASSED 个，已知遗留失败 $KNOWN 个，失败 ${#FAILED[@]} 个"
fi
if [ "$GOLDEN_RAN" -eq 1 ]; then
  [ "$GOLDEN_FAIL" -eq 0 ] && echo "画面比对：通过" || echo "画面比对：失败"
fi

# 失败的测试：显示日志最后 12 行（已去掉无关的 Qt 提示）
if [ "${#FAILED[@]}" -gt 0 ]; then
  echo
  for f in "${FAILED[@]}"; do
    name="$(basename "$f" .py)"
    echo "---- 失败：$f（完整日志：$LOG_DIR/$name.log）----"
    clean "$LOG_DIR/$name.log" | tail -12
    echo
  done
fi

# 顺便报告 git 状态：在哪个分支、有几个文件还没提交
BRANCH="$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo '?')"
DIRTY="$(git status --short 2>/dev/null | wc -l | tr -d ' ')"
echo "git：当前分支 $BRANCH，$DIRTY 个文件有未提交的改动"

if [ "${#FAILED[@]}" -eq 0 ] && [ "$GOLDEN_FAIL" -eq 0 ]; then
  echo "🎉 全部通过"
  exit 0
else
  echo "❌ 有失败项，请看上面的说明"
  exit 1
fi
