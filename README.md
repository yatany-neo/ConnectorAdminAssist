# M365 Admin Companion Agent

A secure, local-first AI assistant for creating Microsoft Graph Connectors.

## 🚀 Getting Started

### 1. Start the Backend Service
This is the "Brain" that runs locally on your machine and handles secure graphing.

1. Open a terminal in VS Code.
2. Run the following command:
   ```bash
   & "c:\Users\neocheng\OneDrive - Microsoft\Documents\M365 Connector\GrowConnectors\Initiative5-AdminCreationAgent\.venv\Scripts\python.exe" backend/app.py
   ```
3. Keep this terminal open.

### 2. Install the Browser Extension
This is the "Interface" that shadows you in the Admin Center.

1. Open **Microsoft Edge** or **Google Chrome**.
2. Navigate to `edge://extensions` or `chrome://extensions`.
3. Enable **"Developer mode"** (toggle in the corner).
4. Click **"Load unpacked"**.
5. Select the `extension` folder in this workspace:
   `...\Initiative5-AdminCreationAgent\extension`
6. You should see "M365 Admin Companion Agent" appear.

### 3. Usage
1. Click the extension icon in your browser toolbar (you might need to pin it).
2. The Side Panel will open.
3. It will connect to your local backend.
4. Click **"Connect to Local Agent"**.
5. **Watch your VS Code Terminal**: It will display a code (e.g., `A1B2C3D4`) and a URL to login.
6. Complete the login in your browser.
7. The extension will turn **Green** and welcome you by name.

## 🔒 Security Note
This agent runs entirely on your local machine (`localhost:8000`). No data is sent to any cloud service other than Microsoft Graph (for the operations you request).
