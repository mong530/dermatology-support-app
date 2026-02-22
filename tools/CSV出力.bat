@echo off
chcp 65001 > nul
REM ============================
REM 管理者向け：ダブルクリックで data.csv を更新
REM 同じフォルダに
REM  - admin_edit_template_unprotected.xlsx
REM  - data.csv
REM  - excel_to_data_csv.py
REM がある前提
REM ============================

python excel_to_data_csv.py --excel admin_edit_template_unprotected.xlsx --csv data.csv

echo.
echo 完了しました。何かERRORが出た場合は、その文をコピーして送ってください。
pause
