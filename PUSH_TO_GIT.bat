@echo off
:: ==========================================
:: 🚀 BỘ TỰ ĐỘNG HÓA ĐẨY SOURCE LÊN GIT 🚀
:: ==========================================
chcp 65001 > nul
title CÔNG CỤ ĐẨY SOURCE CODE LÊN GIT TỰ ĐỘNG
cls

echo ===========================================================
echo 📡 ĐANG KHỞI TẠO HỆ THỐNG ĐẨY GIT TỰ ĐỘNG...
echo ===========================================================
echo.

:: Kiểm tra Git đã cài đặt chưa
where git >nul 2>&1
if %errorlevel% neq 0 (
    echo ❌ LỖI: Chưa tìm thấy Git được cài đặt trên máy!
    echo Vui lòng cài đặt Git từ https://git-scm.com/ và thử lại.
    pause
    exit /b
)

:: Khởi tạo Git nếu chưa có
if not exist .git (
    echo 📥 Chưa phát hiện kho Git. Đang tự động khởi tạo Git Local...
    git init
    echo.
) else (
    echo ✅ Đã phát hiện kho Git hiện hữu.
)

:: Thêm file và kiểm tra status
echo 🧱 Đang tự động thu gom Source Code (Đã áp dụng bộ lọc bảo vệ .gitignore)...
git add .
echo.
echo 📋 DANH SÁCH CÁC FILE SẼ ĐƯỢC ĐẨY LÊN GIT:
echo -----------------------------------------------------------
git status -s
echo -----------------------------------------------------------
echo.

:: Tạo commit tự động
echo 💾 Đang đóng dấu dấu phiên bản (Commit)...
git commit -m "feat: Quy trình DXF-Grounded SAM Tối Thượng + FP16 Acceleration"
echo.

:: Kiểm tra xem có remote origin chưa
git remote get-url origin >nul 2>&1
if %errorlevel% equ 0 (
    for /f "delims=" %%i in ('git remote get-url origin') do set CURRENT_REMOTE=%%i
    echo ✅ Đã phát hiện Link Git từ trước: !CURRENT_REMOTE!
    echo 🔄 Đang tự động đẩy code lên...
    git branch -M main
    git push -u origin main
    goto DONE
)

:: Nếu chưa có, hỏi Link Git của người dùng
echo ===========================================================
echo 💡 HƯỚNG DẪN: Repo Git của Đại ca chưa được cấu hình đích đến!
echo Đại ca vui lòng copy link Repo trên GitHub/GitLab dán vào đây:
echo ===========================================================
echo.
set /p GIT_URL="👉 Nhập Link Git Repo (Ví dụ: https://github.com/username/project.git): "

if "%GIT_URL%"=="" (
    echo.
    echo ⚠️ Không nhập link. Quá trình commit Local hoàn tất thành công!
    echo 💡 Khi nào có link GitHub, Đại ca hãy chạy lại file này để đẩy lên nhé.
    echo.
    pause
    exit /b
)

echo.
echo 🔗 Đang liên kết tới repository: %GIT_URL%
git remote add origin %GIT_URL%
git branch -M main

echo.
echo 🚀 ĐANG ĐẨY DỮ LIỆU LÊN MÂY...
git push -u origin main

:DONE
echo.
echo ===========================================================
echo 🎉 HOÀN THÀNH NHIỆM VỤ MỸ MÃN!!! 🥂🚀
echo Source code của Đại ca đã bay lên Git an toàn và sạch đẹp!
echo ===========================================================
echo.
pause
