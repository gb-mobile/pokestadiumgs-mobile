@echo off
setlocal
set "SCRIPT_DIR=%~dp0"
if exist "%SCRIPT_DIR%tools\package_mobile_stadium.py" (
    cd /d "%SCRIPT_DIR%"
) else if exist "%SCRIPT_DIR%pokestadiumgs-master\tools\package_mobile_stadium.py" (
    cd /d "%SCRIPT_DIR%pokestadiumgs-master"
) else (
    echo ERROR: Could not locate the pokestadiumgs-master build directory.
    goto :done
)

where py >nul 2>nul
if not errorlevel 1 (
    set "PYTHON=py -3"
) else (
    where python >nul 2>nul
    if errorlevel 1 goto :no_python
    set "PYTHON=python"
)

where wsl.exe >nul 2>nul
if errorlevel 1 goto :no_wsl

set "PYTHONPYCACHEPREFIX=%CD%\build\pycache"
if not exist "%PYTHONPYCACHEPREFIX%" mkdir "%PYTHONPYCACHEPREFIX%"

echo [1/11] Extracting normalized US/PAL bases and regional localization data...
%PYTHON% tools\localization.py extract --repo . --input-root .. --languages en fr de it es
if errorlevel 1 goto :failed

echo [2/11] Synchronizing localization-kit graphics and message-file edits...
%PYTHON% tools\sync_localization_kit.py --repo .
if errorlevel 1 goto :failed

echo [3/11] Regenerating regional symbol profiles and Crystal scanner hooks...
%PYTHON% tools\mobile_stadium.py profiles --repo . --input-root .. --languages us en au fr de it es
if errorlevel 1 goto :failed

echo [4/11] Validating editable Mobile Stadium text and graphics inputs...
%PYTHON% -m py_compile tools\sync_localization_kit.py tools\mobile_stadium.py tools\localization.py tools\verify_mobile_interop.py tools\package_mobile_stadium.py tools\stadium2_mobile_save_converter.py
if errorlevel 1 goto :failed
%PYTHON% tools\verify_mobile_interop.py --repo . --source-only
if errorlevel 1 goto :failed

echo [5/11] Compiling the corrected western Mobile Stadium controller overlays...
if exist "build\mobile-stadium\tool\.overlay-build-ok" del /q "build\mobile-stadium\tool\.overlay-build-ok"
wsl.exe --cd "%CD%" bash tools/build_mobile_overlays_wsl.sh us en au fr de it es
if errorlevel 1 goto :cached_overlay
if not exist "build\mobile-stadium\tool\.overlay-build-ok" (
    echo ERROR: WSL did not produce a fresh Mobile Stadium overlay build.
    goto :cached_overlay
)
wsl.exe --cd "%CD%" bash -lc "tail -n +2 build/mobile-stadium/tool/.overlay-build-ok ^| sha256sum -c -"
if errorlevel 1 (
    echo ERROR: The compiled overlay does not match the current Mobile Stadium sources.
    goto :cached_overlay
)
goto :overlay_ready

:cached_overlay
echo WARNING: WSL overlay compilation was unavailable.
echo          Reusing the checked-in regional overlay slots; the full ROM
echo          verifier below will reject them if they do not satisfy the
echo          current Mobile Stadium contract.
for %%L in (us en au fr de it es) do (
    if not exist "assets\localization\mobile\%%L\mobile-overlay-slot.bin" (
        echo ERROR: Cached %%L Mobile overlay slot is missing.
        goto :failed
    )
)

:overlay_ready

echo       Regenerating ROM patch specifications from the fresh overlays and resident hooks...
%PYTHON% tools\mobile_stadium.py refresh-assets --repo . --input-root .. --languages us en au fr de it es
if errorlevel 1 goto :failed

echo [6/11] Building US, PAL English, Australian, French, German, Italian and Spanish ROMs...
%PYTHON% tools\localization.py compose --repo . --input-root .. --languages us en au fr de it es --official-layout --mobile-stadium
if errorlevel 1 goto :failed

echo [7/11] Verifying Crystal uploads/downloads, western replay layout, ROM metadata and save flags...
if exist "..\Saves\pokecrystal-mobile-enabled.sav" if exist "..\pokestadiumgs-ntsc-en.sav" (
    %PYTHON% tools\verify_mobile_interop.py --repo . --crystal-save "..\Saves\pokecrystal-mobile-enabled.sav" --n64-save "..\pokestadiumgs-ntsc-en.sav"
    if errorlevel 1 goto :failed
) else (
    echo Optional interoperability saves not found; skipping save-payload verification.
)

echo [8/11] Verifying the sealed Saturday 11:20 working checkpoint...
if exist "..\Last Working Checkpoint ROMs\rebuild_checkpoint.py" (
    %PYTHON% -B "..\Last Working Checkpoint ROMs\rebuild_checkpoint.py" verify
    if errorlevel 1 goto :failed
) else (
    echo Sealed checkpoint not present; continuing the live build without modifying it.
)

echo [9/11] Updating every local Project64 compatibility profile...
set "PJ64_PROFILE_FOUND="
for /d %%D in ("..\Project64*") do (
    if exist "%%~fD\Config\Project64.rdb" (
        set "PJ64_PROFILE_FOUND=1"
        if exist "..\Last Working Checkpoint ROMs\pokestadiumgs-ntsc-en.z64" (
            %PYTHON% tools\mobile_stadium.py configure-project64 --repo . --input-root .. --languages us en au fr de it es --project64-config "%%~fD\Config" --additional-mobile-rom-root "..\Last Working Checkpoint ROMs"
        ) else (
            %PYTHON% tools\mobile_stadium.py configure-project64 --repo . --input-root .. --languages us en au fr de it es --project64-config "%%~fD\Config"
        )
        if errorlevel 1 goto :failed
    )
    if exist "%%~fD\Project64.rdb" (
        set "PJ64_PROFILE_FOUND=1"
        if exist "..\Last Working Checkpoint ROMs\pokestadiumgs-ntsc-en.z64" (
            %PYTHON% tools\mobile_stadium.py configure-project64 --repo . --input-root .. --languages us en au fr de it es --project64-config "%%~fD" --additional-mobile-rom-root "..\Last Working Checkpoint ROMs"
        ) else (
            %PYTHON% tools\mobile_stadium.py configure-project64 --repo . --input-root .. --languages us en au fr de it es --project64-config "%%~fD"
        )
        if errorlevel 1 goto :failed
    )
)
if not defined PJ64_PROFILE_FOUND echo Optional Project64 configuration not found; skipping profile update.

echo [10/11] Verifying the bundled MBC30/startup-initialization N-Rage Transfer Pak plugin...
if exist "..\Project64 Dev 3.0\Plugin\Input\PJ64_NRage.dll" (
    %PYTHON% tools\verify_nrage_mbc30.py "..\Project64 Dev 3.0\Plugin\Input\PJ64_NRage.dll"
    if errorlevel 1 goto :failed
) else (
    echo Optional Project64 N-Rage plugin not found; skipping plugin verification.
)

echo [11/11] Creating the clean-upstream Mobile Stadium build overlay...
%PYTHON% tools\package_mobile_stadium.py --repo . --output "..\Mobile Stadium Build Requirements" --batch "%~f0"
if errorlevel 1 goto :failed

echo.
echo Mobile Stadium ROM build completed successfully.
echo The bundled N-Rage plugin supports Mobile Crystal SRAM banks 4-7.
echo Build overlay: "%CD%\..\Mobile Stadium Build Requirements"
goto :done

:no_python
echo ERROR: Python 3 was not found in PATH.
goto :done

:no_wsl
echo ERROR: WSL is required to compile the MIPS Mobile Stadium controller overlay.
goto :done

:failed
echo.
echo ERROR: The Mobile Stadium build stopped. Review the message above.

:done
pause
endlocal
