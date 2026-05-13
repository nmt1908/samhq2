@echo off
echo ==============================================================
echo        HQ-SAM 2 AUTO-INSTALLER FOR WINDOWS (CONDA SUC)
echo ==============================================================
echo.

:: Step 1: Find Conda and Activate
echo [*] Dang tim va kich hoat moi truong Conda: suc...
:: Attempt to find Conda path dynamically from standard locations
set CONDAPATH=
if exist "%USERPROFILE%\anaconda3\Scripts\activate.bat" (
    set CONDAPATH="%USERPROFILE%\anaconda3\Scripts\activate.bat"
) else if exist "%USERPROFILE%\miniconda3\Scripts\activate.bat" (
    set CONDAPATH="%USERPROFILE%\miniconda3\Scripts\activate.bat"
) else if exist "C:\ProgramData\anaconda3\Scripts\activate.bat" (
    set CONDAPATH="C:\ProgramData\anaconda3\Scripts\activate.bat"
) else if exist "C:\ProgramData\miniconda3\Scripts\activate.bat" (
    set CONDAPATH="C:\ProgramData\miniconda3\Scripts\activate.bat"
)

if "%CONDAPATH%" == "" (
    echo [!] Canh bao: Khong tim thay duong dan Conda mac dinh.
    echo [*] Se thu chay "conda activate suc" truc tiep...
    call conda activate suc
) else (
    echo [+] Tim thay Conda tai %CONDAPATH%
    call %CONDAPATH% suc
)

:: Verify python location to confirm activation
python -c "import sys; print('Dang su dung Python tai:', sys.executable)"
if %ERRORLEVEL% NEQ 0 (
    echo [!] LOI: Khong the kich hoat moi truong Conda 'suc'.
    echo [!] Anh hay tu mo 'Anaconda Prompt', chay 'conda activate suc' roi tu go tiep nhe!
    pause
    exit /b 1
)

:: Step 2: Install core scientific libraries
echo.
echo [*] Dang cai dat thu vien OpenCV, Matplotlib, Ezdxf, Pillow...
pip install opencv-python matplotlib ezdxf pillow tqdm requests pyyaml pandas
if %ERRORLEVEL% NEQ 0 (
    echo [!] Co loi khi cai dat cac thu vien co ban.
)

:: Step 3: Install PyTorch (Stable for CUDA/CPU)
echo.
echo [*] Kiem tra PyTorch...
python -c "import torch; print('PyTorch da san sang. CUDA:', torch.cuda.is_available())" >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo [*] Dang cai dat PyTorch voi GPU support (se tu cai CPU neu may khong co NVIDIA GPU)...
    pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu121
) else (
    echo [+] Da thay PyTorch san co tren may anh!
)

:: Step 4: Clone HQ-SAM repo from SysCV
echo.
echo [*] Dang tai ma nguon HQ-SAM 2 tu GitHub...
if not exist "sam-hq" (
    git clone https://github.com/SysCV/sam-hq.git
) else (
    echo [+] Da co thu muc sam-hq, bo qua buoc clone.
)

:: Step 5: Build/Install SAM-HQ2 on Windows
echo.
echo [*] Dang bat dau cai dat HQ-SAM 2 (Che do Windows-Safe - Bo qua build CUDA core)...
cd sam-hq\sam-hq2

:: THIS IS THE CRITICAL FIX FOR WINDOWS COMPILATION ERRORS!
set SAM2_BUILD_CUDA=0
pip install -e .

if %ERRORLEVEL% NEQ 0 (
    echo.
    echo [!] GAP LOI KHI BUILD!
    echo [!] Dang thu lai bang cach dung Setup setup.py truc tiep...
    python setup.py build_ext --inplace
    pip install -e .
)

:: Step 6: Download weights
echo.
echo [*] Dang thiet lap thu muc Checkpoint & Tai model...
if not exist "checkpoints" mkdir checkpoints
cd checkpoints

:: Download large HQ-SAM2 checkpoint
if not exist "sam2.1_hq_hiera_large.pt" (
    echo [*] Dang tai file trong so HQ-SAM 2 Large (Tam ~800MB)...
    echo [*] Vui long cho, co the mat 2-5 phut tuy vao toc do mang...
    curl -L -o sam2.1_hq_hiera_large.pt "https://huggingface.co/lkeab/hq-sam/resolve/main/sam2.1_hq_hiera_large.pt"
) else (
    echo [+] File sam2.1_hq_hiera_large.pt da ton tai!
)

:: Download base HQ-SAM2 checkpoint (optional, faster fallback)
if not exist "sam2.1_hq_hiera_base_plus.pt" (
    echo [*] Dang tai them file nho gon Base Plus de du phong...
    curl -L -o sam2.1_hq_hiera_base_plus.pt "https://huggingface.co/lkeab/hq-sam/resolve/main/sam2.1_hq_hiera_base_plus.pt"
)

cd ..
cd ..
cd ..

echo.
echo ==============================================================
echo [+] HOAN TAT CAI DAT MOI TRUONG VA MO HINH HQ-SAM 2!
echo [+] Anh hay bat file Python 'dxf_sam_matching.py' de su dung.
echo ==============================================================
echo.
pause
