@echo off
setlocal
call "C:\Program Files\Microsoft Visual Studio\2022\Community\VC\Auxiliary\Build\vcvars64.bat"
if errorlevel 1 exit /b %errorlevel%
cd /d "%~dp0.."
if not exist build\cuda mkdir build\cuda
nvcc -allow-unsupported-compiler -O3 -arch=sm_89 cuda\acd_partition_bench.cu -o build\cuda\acd_partition_bench.exe
exit /b %errorlevel%
