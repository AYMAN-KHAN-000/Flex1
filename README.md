# FLEX

FLEX is a Windows desktop AI agent designed to translate natural-language instructions into structured tool calls, validate them, and execute safe, verifiable actions on the system.

## What FLEX is

The system follows a clear architecture:

- User request
- FLEX UI
- Agent orchestrator
- AI provider (OpenRouter by default)
- Tool registry
- Security and permission checks
- Execution
- Observation and verification
- Response to the user

This keeps the LLM from directly running arbitrary commands on the machine. Instead, the model requests structured tools, and FLEX validates and executes them locally.

## Identity and creator

FLEX is an independently developed personal AI desktop assistant, created and developed by
Ayman Khan. FLEX is separate from its configurable AI provider and model; OpenRouter provides
AI infrastructure and is not FLEX's creator or owner.

- Portfolio: [https://aymanweb.vercel.app](https://aymanweb.vercel.app)
- Personal website: [https://aymanbio.vercel.app](https://aymanbio.vercel.app)
- GitHub: [https://github.com/AYMAN-KHAN-000](https://github.com/AYMAN-KHAN-000)

## Project architecture

```text
FLEX/
├── main.py
├── requirements.txt
├── .env.example
├── README.md
├── flex/
│   ├── __init__.py
│   ├── config.py
│   ├── logging_config.py
│   ├── ai/
│   │   ├── __init__.py
│   │   ├── provider.py
│   │   ├── openrouter.py
│   │   └── schemas.py
│   ├── core/
│   │   ├── agent.py
│   │   ├── context.py
│   │   ├── executor.py
│   │   ├── observer.py
│   │   ├── planner.py
│   │   ├── state.py
│   │   └── verifier.py
│   ├── security/
│   │   ├── permissions.py
│   │   ├── policies.py
│   │   └── validator.py
│   ├── tools/
│   │   ├── app_control.py
│   │   ├── filesystem.py
│   │   └── registry.py
│   ├── memory/
│   │   ├── database.py
│   │   └── usage_memory.py
│   ├── research/
│   │   ├── research_engine.py
│   │   ├── search_provider.py
│   │   ├── source_evaluator.py
│   │   ├── result_ranker.py
│   │   └── research_history.py
│   ├── context/
│   │   ├── location_manager.py
│   │   ├── time_service.py
│   │   └── weather_service.py
│   └── ui/
│       ├── chat_renderer.py
│       └── main_window.py
├── data/
├── logs/
└── tests/
```

## Installation

Requires Python 3.13+.

```bash
python -m pip install -r requirements.txt
```

## Build the Windows executable

Install the project dependencies and PyInstaller in the same Python environment, then build the single-file desktop app:

```powershell
python -m pip install -r requirements.txt
python -m pip install pyinstaller
python -m PyInstaller --clean --noconfirm Flex.spec
```

The windowed executable is created at `dist/Flex.exe`. Copy it to a writable folder to run it. Optional configuration can be supplied in a `.env` file beside the executable; do not distribute a `.env` containing API keys. Logs and the local memory database are stored under `%LOCALAPPDATA%\FLEX`. The Playwright browser and speech recognition model are downloaded separately when needed; see the browser and voice setup notes below.

Create a local `.env` file from `.env.example` and add your OpenRouter API key:

```bash
copy .env.example .env
```

Then update the values in `.env`.

## OpenRouter configuration

FLEX uses an OpenRouter-compatible API endpoint. The API key is read from `.env` and never hard-coded in source files.

```env
OPENROUTER_API_KEY=your_key_here
OPENROUTER_MODEL=openai/gpt-4o-mini
```

If the API key is not configured, the application still starts but the AI provider will report a configuration error until the key is set.

## Discord integration

Open **Discord** in the FLEX sidebar, enter a Discord bot token and an activation password, then choose **SAVE bot info**. FLEX encrypts the saved token with the current Windows user's DPAPI profile and stores activated Discord IDs locally. The bot reconnects when FLEX starts if the integration was previously enabled. **Disconnect bot** stops the connection while retaining credentials and activated users; **Reset/delete info** removes them.

In the Discord Developer Portal, enable the **Message Content Intent** and invite the application with the `bot` and `applications.commands` scopes. Grant it permission to view and send messages in the channels where it should respond. FLEX registers `/guide` and `/flex-activate`; DMs accept regular messages, while server messages must start with the configured prefix (default `-flex`).

Public users can use FLEX's AI chat but receive no computer-control tools. PC requests require individual activation. Sensitive actions require confirmation at the FLEX computer; terminal execution, clipboard access, local memory, task management, and automation are not exposed through Discord. High-risk actions remain gated even if local confirmation is disabled in Settings. Remove users in the Discord settings panel to revoke access; reset/delete stops the bot and clears its saved configuration and authorizations. **Recent activity** shows the last 50 command outcomes in memory (Discord IDs and outcome only; message contents and activation passwords are not recorded).

## Running FLEX

```bash
python main.py
```

Run the test suite with:

```powershell
python -m pytest -q
```

## Chat response formatting

AI replies use the Qt Markdown renderer in `flex/ui/chat_renderer.py`, independent of the
configured AI provider. It formats headings, emphasis, lists, blockquotes, tables, links, and
fenced code blocks; code can be copied from its context menu, and wide code or tables can be
scrolled horizontally. Raw HTML is not rendered. Responses are rendered after completion;
streaming is not currently used. This uses the existing PySide6 dependency.

## Overlay and edge behavior

FLEX now includes a lightweight top-edge overlay foundation: an always-on-top, transparent hover trigger that opens only when the gray edge line is hovered. Its compact bar stays fixed in size instead of turning into a full chat panel. This is the first overlay iteration described in the product spec, and it can be customized with environment variables such as:

```env
FLEX_EDGE_POSITION=top
FLEX_EDGE_LINE_COLOR=#a6a6a6
FLEX_EDGE_LINE_LENGTH=210
FLEX_EDGE_LINE_THICKNESS=6
FLEX_EDGE_LINE_OPACITY=0.9
FLEX_BAR_WIDTH=420
FLEX_BAR_HEIGHT=72
```

The idle indicator is a short gray line aligned with the physical screen edge, with a subtle black shadow. Hovering the line slides the glossy, rounded notch in from beyond the screen edge to its resting position just beneath the indicator, which stays layered over the notch; the notch remains open after sending and slides back out when you click outside it. Its smaller purple eyes smoothly follow the pointer with a wider gaze and blink in both modes. Activating voice mode with the mic starts voice chat, centers the eyes, and hides the text controls; the notch stays open if you click elsewhere while voice chat is active. In voice mode, FLEX shows a happy expression for four seconds after thanks or appreciation, then goes to sleep after 20 seconds without speech or chat activity. Main-window messages and notch clicks wake it; say “sleep,” “Flex sleep,” or “go sleep mode” to sleep immediately, and say “wake” or start speaking to wake it. The sleep expression animates three purple Zs using the supplied image, and its eyes stay still. Explicit PC sleep requests, such as “make my PC sleep,” continue to be handled as requests to the assistant. Spoken requests are transcribed in English on-device with the free `faster-whisper` `small.en` model; FLEX reads replies aloud using the built-in Windows text-to-speech voice. The Whisper recognition model (about 500 MB) downloads on first use; recognition runs offline and microphone audio is not uploaded. FLEX replies still use the configured OpenRouter AI provider. The overlay uses matching mic and send icons and stays minimal and reserved for quick interaction while the main FLEX app continues to provide the full chat and settings experience. In **Settings**, choose **Start overlay and close main window** to hide the main window while keeping FLEX available from the screen edge. Use the FLEX system-tray menu to reopen the main window or exit the application.

The supplied FLEX image has subtly rounded transparent corners and is used for the application window, Windows taskbar, and system tray icon from `flex/ui/assets/flex_icon.png`. A multi-resolution Windows icon is also provided at `flex/ui/assets/flex_icon.ico` for packaging. FLEX sets a stable Windows application ID so its running window is associated with the FLEX taskbar icon.

## Tool architecture

Tools are registered in a central registry and contain:

1. A unique tool name
2. A description
3. An input schema
4. A permission level
5. An executor function
6. A verification step
7. Timeouts and error handling

This keeps execution predictable and reviewable.

FLEX limits each request to eight tool-call rounds and twelve tool calls, blocks an identical
operation after it fails, and only reports verified tool outcomes as successful. Multiple
protected actions requested in the same model step are presented together for one approval;
system-critical actions requested in later steps still require their own confirmation. Final
responses are kept brief and do not expose internal tool names, logs, scripts, or commands.

## Chat-based settings

FLEX can update supported settings through chat using validated, registered tools. For example:

- “Set my study folder to `C:\Users\NPL\Downloads\tests`”
- "What's my current study folder?"
- “Change my study folder to `D:\School\Books`”
- “Remove my study folder”
- “Save my location as Savar, Bangladesh”
- “What’s my saved location?”
- “Use Asia/Dhaka for local time”
- “Forget my saved location”

Study folders must already exist and be accessible; FLEX does not create them. Saved locations are resolved to a timezone before being stored, and ambiguous place names require clarification. A preferred timezone affects FLEX’s local-time answers only, not Windows’ system timezone. Deleting a saved location requires confirmation. FLEX reports only settings that have registered handlers as supported.

Application discovery checks Windows `PATH`, common install locations, Start Menu shortcuts, and registered Start apps. Known aliases include Chrome, Edge, VS Code, Notepad, File Explorer, Camera, and OneNote. Windows Settings can be launched with its `ms-settings:` URI. FLEX can search the web or open a website directly in the default browser or a named browser, open social media accounts on YouTube, Instagram, TikTok, X/Twitter, Facebook, Twitch, and Threads, and type requested text into a named, open application window. For social accounts, FLEX opens a confirmed handle directly and searches the platform for a display name without a confirmed handle; retired YouTube `/c/` links are redirected to channel search. It can create a new text file without overwriting an existing file and open it in a selected application such as VS Code. Filesystem tools are restricted to the current user's profile. Use a relative path such as `Desktop/FLEX_TEST`; FLEX resolves it against the active Windows Desktop location, including a Desktop redirected to OneDrive.

FLEX can also open the Windows Start menu and Search, locate or launch apps, search the web and permitted user files, list running apps, and close an app. Lock, sleep, sign out, restart, shutdown, and closing an app require an explicit in-app confirmation when `REQUIRE_CONFIRMATION=true`. These controls use fixed Windows actions; FLEX does not accept or execute arbitrary shell commands.

## Windows utility tools

FLEX includes on-demand clipboard, default-output volume, primary-display brightness, and screenshot tools. Clipboard access is disabled by default; enable **Allow FLEX to access the clipboard when I ask** in Settings. FLEX does not monitor or save clipboard history. Clipboard text is read only for an explicit request, and clipboard images or files are classified without returning their contents or paths.

Volume controls query and verify the current/default Windows output device, retaining mute as a separate state from the volume percentage. Brightness controls query the primary display and verify the reported result; devices Windows cannot control are reported as unsupported. Screenshot tools capture the full screen or active window and save a collision-safe, timestamped PNG locally. Screenshots are not automatically uploaded or sent for AI analysis.

Optional environment settings:

```env
FLEX_CLIPBOARD_ACCESS=false
FLEX_SCREENSHOT_DIRECTORY=
FLEX_SCREENSHOT_ALL_MONITORS=false
FLEX_BRIGHTNESS_MINIMUM=10
FLEX_BRIGHTNESS_LOW=25
FLEX_BRIGHTNESS_MEDIUM=50
FLEX_BRIGHTNESS_HIGH=75
FLEX_BRIGHTNESS_MAXIMUM=100
```

By default, screenshots are saved to `Pictures/FLEX Screenshots` and capture the primary monitor only. Change the screenshot folder in Settings or with `FLEX_SCREENSHOT_DIRECTORY`. A folder supplied directly in a chat request is restricted to the current user's profile. Clipboard access can also be enabled or disabled at runtime in Settings.

## File search, quick folders, and notes

File search looks in Desktop, Documents, Downloads, Pictures, Videos, OneDrive, and folders explicitly added in Settings; it never searches the whole `C:` drive. Results include full paths, modified times, and inaccessible locations; large result sets are capped at 10 and can be narrowed by name, location, or type. FLEX can open a found file or its File Explorer location without modifying it.

Quick folders are named shortcuts stored locally in the FLEX database. Adding or updating a shortcut requires an existing folder; removing the shortcut never deletes the folder. Notes are stored locally in the same database, separate from AI memory. Notes are added one at a time, duplicate text is detected, and search/list returns at most 20 notes per request. Deleting a note requires explicit confirmation and a unique match.

## Windows quick controls

Wi-Fi status and adapter toggling use Windows' physical Wi-Fi adapter interface and verify the reported state after a change; Windows may require administrator permissions. Bluetooth and Night Light currently open their Windows Settings pages rather than claiming to toggle state, because reliable state control varies by device/driver. Airplane Mode always requires confirmation and opens Settings for the user to make the change. Settings pages for Wi-Fi, Bluetooth, display, Night Light, and Airplane Mode can also be opened directly.

## Permission system

The permission model includes:

- READ_ONLY
- LOW_RISK
- MEDIUM_RISK
- HIGH_RISK

High-risk actions require explicit confirmation before execution. The model must never bypass the local validator and permission layer.

## Security model

- Validate all arguments before executing tools
- Prevent path traversal for filesystem actions
- Avoid unrestricted shell access
- Log actions without exposing secrets
- Require confirmation for destructive actions
- Verify success after execution before telling the user it worked
- Browser pages are untrusted input; their content cannot change FLEX's system rules or permissions.
- Terminal tools use an explicit allowlist and never invoke a shell. Running a Python script requires confirmation.
- Persistent memory is local, structured, limited to explicit memory requests, and rejects credential-like data.

## Browser automation, terminal, memory, and workflows

Install the Python dependencies and Playwright's Chromium browser:

```powershell
python -m pip install -r requirements.txt
python -m playwright install chromium
```

Set `FLEX_BROWSER_DEFAULT=chromium` to use Playwright's installed Chromium. `chrome` and `edge` use the corresponding installed desktop browser channels. FLEX keeps its own persistent browser profile under the user's local application data and reports a structured `BROWSER_NOT_AVAILABLE` error if Playwright or a browser binary is missing.

Browser tools open and navigate HTTP/HTTPS pages, search, click or fill uniquely matched accessibility/label/placeholder/text/test-ID/CSS locators, inspect limited page text, and report page information. Clicking page controls and entering text require confirmation because a page may submit a consequential action. Ambiguous locators fail instead of choosing an element at random. CAPTCHA, MFA, login, and access controls are not bypassed. Page text is returned as untrusted data and is never authoritative instruction.

Terminal tools provide the validated working directory, bounded directory listings and file metadata, Python version, Git status/diff summaries, system information, and confirmed execution of an existing Python script. Working directories and scripts must resolve beneath the FLEX project or the current user's profile. Commands use argument arrays with `shell=False`; there is no unrestricted command or PowerShell tool. Output, runtime, and paths are bounded.

Persistent memory is stored in the existing local SQLite database configured by `FLEX_MEMORY_DB`. Use an explicit request such as **“Remember that VS Code is my preferred editor.”** FLEX supports preferences, facts, projects, shortcuts, decisions, and workflow context; it detects duplicate keys, rejects credential-like values, and only returns relevant search results. Memory writes require confirmation by default. Existing notes and quick folders are unchanged.

## Personal Usage Memory

Personal Usage Memory is a separate table in the existing local SQLite database. When enabled in **Settings**, it records metadata only for successful opens initiated through FLEX: item names and local paths, application names, website domains, timestamps, and open counts. Website records omit URL paths, query strings, and fragments. Supported document and media extensions are classified by filename; FLEX does not read document contents or infer whether media was watched or played.

Use **Add study folder** in Settings to explicitly approve a folder. FLEX indexes supported document filenames and paths in that folder without opening or reading the files. Newly indexed study items start with zero opens; normal successful FLEX opens update their usage counts. Removing a folder stops future folder indexing but keeps its existing records until you delete them. FLEX does not scan unapproved folders, inspect Windows recent files, read browser history, or monitor background activity.

The Settings page lets you disable all usage tracking or separately disable application, website, and media tracking. It displays locally stored items with recent/frequent sorting and search, identifies missing or inaccessible local files, and allows you to remove one usage item or clear usage history. **Explicitly saved memories** are managed separately in the same page; remove any selected memory there. Successful opens are counted, while failed actions are not. Records are deduplicated by normalized local path or website domain and sorted by recency or open frequency.

Usage-memory records are local-only and are not supplied as OpenRouter context. FLEX never records passwords, cookies, authentication tokens, keystrokes, clipboard contents, or screenshots. Tracking can be disabled at any time without disabling the assistant.

## Smart Research and live context

**Smart Research** is a multi-query workflow using Yahoo Search's HTML results and the source titles/snippets it returns. Results are deduplicated and ranked with a simple topic-relevance heuristic. The heuristic is not a fact check and does not inspect every destination page. FLEX labels snippets as untrusted, preserves citations, and does not claim that prices, reviews, shipping, availability, or specifications are verified unless the returned snippet explicitly states them. If a search provider returns a challenge page, FLEX reports a search failure rather than presenting it as proof that no products exist. For product requests with a maximum budget, FLEX will not recommend a result without explicit in-budget price evidence; when snippets are insufficient, it can use the configured browser tools to inspect official retailer pages before recommending. It can open a saved selected result in the configured browser when requested. It never completes a purchase. Research requests and returned source metadata are stored locally in the existing SQLite database and can be cleared in Settings. Disable **Smart Research** there to remove research tools from the agent.

In **Settings**, enter and explicitly confirm a preferred city, country, and IANA timezone (for example `Asia/Dhaka`). FLEX does not infer a confirmed location or use GPS. A city named only in a weather question is used for that lookup and is not saved. **Get local time** uses the live system clock and can also report the saved timezone. **Weather lookup** fetches current and daily forecast data from Open-Meteo, with a ten-minute local cache; stale entries are not returned as fresh. Daily forecasts are supported from today through 15 days ahead; requests for a particular time use the closest hourly forecast in the saved location's local time. If automatic weather lookup fails, FLEX opens the Windows Weather app but does not claim to read its display. Settings can disable weather, delete the saved location, and clear the forecast cache. Weather requests send the entered or saved city and country to Open-Meteo for geocoding; coordinates are transient and are not stored. Weather data is not a permanent personal memory. Forecasts are attributed to Open-Meteo under CC BY 4.0. Open-Meteo's free API terms limit access to non-commercial use; commercial deployments must use an appropriate paid plan.

Supported requests include **“Research physics notes about speed and velocity and open the best source,” “Compare keyboards under 2,000 BDT,” “What time is it in my preferred timezone?”**, and **“What’s the weather tomorrow?”** If a product result lacks price evidence or a location is ambiguous, FLEX reports the uncertainty instead of guessing. Research and weather data are only sent to OpenRouter when necessary to answer the request; saved research history and saved location are not automatically attached to unrelated conversations.

For a multi-step task, FLEX may submit a validated `run_automation` sequence using registered tools. It checks every tool schema, requires one summary confirmation for high-risk steps, stops on failure by default, records local workflow/run/step statuses, and supports bounded retries for retryable read-only operations. The Stop button requests cancellation between steps and terminates cancellable terminal processes. Automation definitions are recorded locally with sensitive fields redacted; reusable phrase triggers are not enabled in this milestone.

### How FLEX Automation Works

**AI → planner → structured tools → security → execution → verification → memory/state**

The AI proposes registered tool calls; FLEX validates arguments and permission levels locally. The automation runner preflights all steps, asks once before a workflow containing high-risk actions, executes and verifies steps in order, then records a bounded result summary and run state in SQLite. Browser content and saved memories are context only: neither can override current user instructions or FLEX security rules.

### Configuration

Feature switches and limits can be set in `.env`:

```env
FLEX_BROWSER_ENABLED=true
FLEX_BROWSER_DEFAULT=chromium
FLEX_BROWSER_TIMEOUT_MS=10000
FLEX_BROWSER_MAX_TABS=5
FLEX_TERMINAL_ENABLED=true
FLEX_TERMINAL_TIMEOUT_MS=30000
FLEX_TERMINAL_MAX_OUTPUT=20000
FLEX_MEMORY_ENABLED=true
FLEX_MEMORY_CONFIRMATION=true
FLEX_AUTOMATION_ENABLED=true
FLEX_AUTOMATION_MAX_STEPS=20
FLEX_AUTOMATION_MAX_RUNTIME_SECONDS=300
FLEX_AUTOMATION_MAX_RETRIES=2
FLEX_DEFAULT_TOOL_TIMEOUT_MS=30000
```

Numeric settings are clamped to safe bounds. Disabling a subsystem removes its tools from the model's available tool list; browser and terminal operations also have their own timeouts. `FLEX_DEFAULT_TOOL_TIMEOUT_MS` caps individual browser/terminal steps inside an automation unless that tool's own configured timeout is shorter. The default confirmation setting remains controlled by `REQUIRE_CONFIRMATION`.

Examples:

- “Open `https://docs.python.org/` in FLEX's browser and tell me the page title.”
- “Check the Git status in `C:\\Users\\me\\Projects\\FLEX`.”
- “Remember that VS Code is my preferred editor.”
- “Open VS Code, then check the current project's Git status.”

Troubleshooting:

- If browser automation reports `BROWSER_NOT_AVAILABLE`, install Playwright Chromium with `python -m playwright install chromium`, or select `chrome`/`edge` when that browser is installed.
- If terminal tools reject a directory, choose a folder inside the FLEX project or current user's profile.
- If a Python script is not run, approve its high-risk confirmation prompt; scripts execute with the current user's permissions.
- OpenRouter billing/model errors are separate from local browser, terminal, memory, and automation features.

Press Enter in the chat input to send a message. Select **Start Voice Chat** to use local `faster-whisper` speech recognition; FLEX submits each recognized phrase after two seconds of silence, displays it and the reply in the same chat, and reads the reply aloud using the built-in Windows text-to-speech voice. Voice requests use the same response path as typed requests. Failed actions are reported briefly and are never described as successful. Microphone audio is processed locally; only the recognized text is sent to OpenRouter. Voice chat requires a microphone.

To preview a notch expression without sending a request to OpenRouter, type `natural9090-test <emotion ID>` in the chat input and press Enter. The preview lasts four seconds. Supported IDs: `neutral`, `happy`, `sleep`, `love`, `wonder`, and `confused`. Typing an unsupported ID after the test command shows the valid IDs without sending a request. Typing an emotion ID by itself also previews it.

## Task Manager

FLEX can schedule local task reminders from chat (through the configured AI provider) or directly in **Settings → Task Manager**. For example, ask “Schedule study tomorrow from 5 PM to 8 PM” or “Remind me to exercise tomorrow at 7 AM.” FLEX asks for missing or ambiguous scheduling details rather than inventing them. Tasks can be listed, edited, paused, resumed, cancelled, deleted, and configured for start/end notifications. The Settings panel also includes task creation, notification controls, display duration, and completed-task cleanup.

Tasks are stored separately from conversation memory in `tasks.json` beside the project in development, or in `%LOCALAPPDATA%\FLEX\tasks.json` in a packaged build. FLEX checks the schedule every 15 seconds while the app remains running, including when minimized to the system tray; start/end notifications slide in from the configured notch edge, are queued, and can be clicked to dismiss. Setting a task only shows a “Task set” confirmation; notifications are reserved for the scheduled start and end. The selected preferred IANA timezone is used (Asia/Dhaka if none is set). The scheduler does not make periodic AI requests. If FLEX or Windows is shut down, no notification can be shown at the scheduled moment; after restart, still-running tasks are recovered, reminders more than 10 minutes late are skipped, and end notifications more than 12 hours late are skipped.

## Development roadmap

This repository currently includes the foundation and first browser/terminal/memory/automation milestone:

- PySide6 interface
- Chat input/output
- Tool registry
- OpenRouter provider abstraction
- Security/permission validation
- Logging
- Configuration
- Basic agent loop
- Windows voice chat with local speech recognition and playback
- Windows Start/Search and confirmed session/power controls
- On-demand Windows clipboard, audio, display-brightness, and screenshot tools
- Persistent local structured memory and bounded multi-step automation
- Playwright browser session and allowlisted terminal tools

## Troubleshooting

- If PySide6 fails to import, reinstall dependencies and ensure Python 3.13 is active.
- If OpenRouter fails, verify `.env` values and API quota.
- If the app does not start, check the terminal output for runtime errors.

## Adding a new tool

1. Create a tool module under `flex/tools/`
2. Define the arguments and validation model
3. Assign the permission level
4. Register the tool in the registry
5. Add an executor and verification method
6. Add tests for success and error cases

## License

This project is a working implementation of the FLEX specification and is intended for local, personal use in desktop automation scenarios.
#   F l e x 1  
 