@echo off
REM Script to run Claude Code with Gemini router and model selection

setlocal enabledelayedexpansion

REM Define available models
set "models[0]=gemini-2.5-pro"
set "models[1]=gemini-2.5-flash"
set "models[2]=gemini-2.0-flash"
set "models[3]=gemini-2.0-flash-thinking-exp"
set "models[4]=gemini-1.5-pro"
set "models[5]=gemini-1.5-flash"

echo.
echo ^🚀 Starting Claude Code with Gemini router...
echo.

REM Check if model passed as argument
if not "%~1"=="" (
    set "model=%~1"
    echo Using model: !model!
    goto :setup
)

REM Show model selection menu
echo Available Gemini models:
echo.
echo   1^) gemini-3-pro
echo   2^) gemini-3-flash
echo   3^) gemini-2.5-pro
echo   4^) gemini-2.5-flash
echo   5^) gemini-2.5-flash-lite
echo   6^) gemini-2.0-flash
echo   7^) gemini-2.0-flash-thinking-exp
echo   8^) gemini-1.5-pro
echo   9^) gemini-1.5-flash
echo.

set /p choice="Select model (1-9) [default: 1]: "
if "!choice!"=="" set "choice=1"

if "!choice!"=="1" set "model=gemini-3-pro"
if "!choice!"=="2" set "model=gemini-3-flash"
if "!choice!"=="3" set "model=gemini-2.5-pro"
if "!choice!"=="4" set "model=gemini-2.5-flash"
if "!choice!"=="5" set "model=gemini-2.5-flash-lite"
if "!choice!"=="6" set "model=gemini-2.0-flash"
if "!choice!"=="7" set "model=gemini-2.0-flash-thinking-exp"
if "!choice!"=="8" set "model=gemini-1.5-pro"
if "!choice!"=="9" set "model=gemini-1.5-flash"

if "!model!"=="" (
    echo Invalid selection. Using default: gemini-3-pro
    set "model=gemini-3-pro"
)

:setup
REM Get base URL from environment or use router.vseek.app
if "!ANTHROPIC_BASE_URL!"=="" (
    set "base_url=https://router.vseek.app"
) else (
    set "base_url=!ANTHROPIC_BASE_URL!"
)

REM Get API key from environment, .env file, or prompt user
if "!ANTHROPIC_API_KEY!"=="" (
    if exist .env (
        REM Try to read PROXY_API_KEY from .env
        for /f "tokens=2 delims==" %%A in ('findstr /R "PROXY_API_KEY=" .env') do (
            set "api_key=%%A"
        )
    )

    if "!api_key!"=="" (
        set /p api_key="Enter ANTHROPIC_API_KEY (or press Enter for 'dummy'): "
        if "!api_key!"=="" set "api_key=dummy"
    )
) else (
    set "api_key=!ANTHROPIC_API_KEY!"
)

echo Configuration:
echo   Model: !model!
echo   Base URL: !base_url!
echo   API Key: !api_key:~0,10!...
echo.

REM Set environment variables and launch Claude Code
set "ANTHROPIC_BASE_URL=!base_url!"
set "ANTHROPIC_API_KEY=!api_key!"

claude --model !model!

endlocal
