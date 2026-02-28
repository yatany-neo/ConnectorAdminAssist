import asyncio
import logging
import os
import datetime
import uuid
from typing import Dict, Optional
from contextlib import asynccontextmanager
from functools import partial
from dotenv import load_dotenv
from openai import AsyncAzureOpenAI
from pydantic import BaseModel
from fastapi import FastAPI, HTTPException, Header
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from azure.identity import DeviceCodeCredential
from azure.core.credentials import AccessToken
from msgraph import GraphServiceClient

# Load environment variables
load_dotenv()

# Configure logging to see the device code
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("m365-agent-backend")

class SessionState:
    def __init__(self):
        self.client = None
        self.credential = None
        self.is_authenticated = False
        self.auth_code_info = None
        self.auth_task = None
        self.ai_client = None # Azure OpenAI Client - Shared or per session? simpler per session if keys differ, but here env is shared.
        # actually ai_client is from env, so it can be shared or just re-inited. 
        # For efficiency, let's keep ai_client global or just init it once.

# Global Sessions Store
sessions: Dict[str, SessionState] = {}
global_ai_client = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    logger.info("Backend Service Starting...")
    global global_ai_client
    
    # Initialize Azure OpenAI Client
    try:
        if os.getenv("AZURE_OPENAI_API_KEY"):
            global_ai_client = AsyncAzureOpenAI(
                azure_endpoint=os.getenv("AZURE_OPENAI_ENDPOINT"),
                api_key=os.getenv("AZURE_OPENAI_API_KEY"),
                api_version=os.getenv("AZURE_OPENAI_API_VERSION", "2024-08-01-preview")
            )
            logger.info("Azure OpenAI Client Initialized.")
        else:
            logger.warning("Azure OpenAI Key not found. AI features will be disabled.")
    except Exception as e:
        logger.error(f"Failed to init OpenAI: {e}")
        
    yield
    # Shutdown logic
    logger.info("Backend Service Shutting Down...")
    # Clean up sessions?
    sessions.clear()

app = FastAPI(title="M365 Admin Companion Agent Backend", lifespan=lifespan)



# Wrapper to make DeviceCodeCredential non-blocking
class AsyncDeviceCodeCredential:
    def __init__(self, *args, **kwargs):
        self._sync_cred = DeviceCodeCredential(*args, **kwargs)

    async def get_token(self, *scopes, **kwargs) -> AccessToken:
        loop = asyncio.get_running_loop()
        # Run the blocking get_token in a separate thread
        return await loop.run_in_executor(
            None, 
            lambda: self._sync_cred.get_token(*scopes, **kwargs)
        )
            
    async def close(self):
        pass
    
    async def __aenter__(self):
        return self
        
    async def __aexit__(self, *args):
        pass

# CORS for local browser extension
# class AgentState removed in favor of SessionState and sessions dict

@app.on_event("startup")
async def startup_event():
    logger.info("Backend Service Started. (Legacy Handler)")

def device_code_callback(session_id, verification_uri, user_code, expires_on):
    """
    Callback to capture the device code and URI for a specific session.
    """
    logger.info(f"CAPTURED DEVICE CODE for {session_id}: {user_code}")
    safe_uri = verification_uri if verification_uri else "https://microsoft.com/devicelogin"
    safe_code = user_code if user_code else "UNKNOWN_CODE"

    if session_id in sessions:
        sessions[session_id].auth_code_info = {
            "verification_uri": safe_uri,
            "user_code": safe_code,
            "expires_on": expires_on.isoformat() if hasattr(expires_on, 'isoformat') else str(expires_on),
            "message": "Please sign in to authenticate."
        }

async def perform_login(session_id: str):
    """
    Background task to wait for user login for a specific session.
    """
    try:
        if session_id not in sessions:
            return
            
        session = sessions[session_id]
        if not session.client:
            return

        # Trigger the auth flow by making a call
        await session.client.me.get()
        
        # Update session state
        session.is_authenticated = True
        session.auth_code_info = None 
        logger.info(f"Authentication Background Task Completed Successfully for {session_id}.")
    except Exception as e:
        logger.error(f"Authentication Background Task Failed for {session_id}: {e}")
        if session_id in sessions:
            sessions[session_id].auth_code_info = {"error": str(e)}

@app.get("/")
def read_root(x_session_id: Optional[str] = Header(None)):
    session_auth_status = "unauthenticated"
    if x_session_id and x_session_id in sessions:
        if sessions[x_session_id].is_authenticated:
            session_auth_status = "authenticated"
            
    return {
        "status": "running", 
        "service": "M365 Admin Companion Backend",
        "auth_status": session_auth_status,
        "session_id": x_session_id
    }

@app.post("/auth/login")
async def login(x_session_id: Optional[str] = Header(None)):
    """
    Initiates the Device Code Flow in the BACKGROUND.
    """
    # If no session ID provided, create one? 
    # Logic: Client MUST provide a session ID to maintain continuity.
    # But for first request, we can accept missing and return new?
    # Better: Frontend generates UUID.
    
    if not x_session_id:
        return {"status": "error", "message": "Missing X-Session-ID header"}
        
    session_id = x_session_id
    
    if session_id not in sessions:
        sessions[session_id] = SessionState()
        
    session = sessions[session_id]

    if session.is_authenticated:
         return {"status": "success", "message": "Already authenticated"}

    # Reset state
    session.auth_code_info = None
    
    client_id = "14d82eec-204b-4c2f-b7e8-296a70dab67e" 
    
    # Use partial to pass session_id to callback
    callback_with_session = partial(device_code_callback, session_id)
    
    session.credential = AsyncDeviceCodeCredential(
        client_id=client_id,
        prompt_callback=callback_with_session
    )
    
    scopes = ["https://graph.microsoft.com/ExternalConnection.ReadWrite.OwnedBy", "https://graph.microsoft.com/User.Read"]
    session.client = GraphServiceClient(credentials=session.credential, scopes=scopes)
    
    # Start the actual login process in the background
    session.auth_task = asyncio.create_task(perform_login(session_id))

    return {
        "status": "pending_interaction", 
        "details": "Authentication started in background.", 
        "instruction": "Poll /auth/code to get the device code."
    }

@app.get("/auth/code")
def get_auth_code(x_session_id: Optional[str] = Header(None)):
    """
    Returns the current device code info for the frontend to display.
    """
    if not x_session_id or x_session_id not in sessions:
        return {"status": "waiting", "message": "Session not found."}
        
    session = sessions[x_session_id]
    
    if session.is_authenticated:
        return {"status": "authenticated"}
    
    if session.auth_code_info:
        return {"status": "present", **session.auth_code_info}
            
    return {"status": "waiting", "message": "Waiting for device code generation..."}


from pydantic import BaseModel

class ChatRequest(BaseModel):
    message: str
    context_url: str = ""
    dom_snippet: str = "" # New field for page content

@app.post("/agent/chat")
async def chat(request: ChatRequest, x_session_id: Optional[str] = Header(None)):
    """
    Intelligent Chat using Azure OpenAI GPT-4o.
    """
    # If AI is not configured, fallback to rule-based or error
    if not global_ai_client:
        return {
            "response": "⚠️ **Azure OpenAI is not configured.** Please set up your `.env` file with Endpoint and Key."
        }

    user_msg = request.message
    # Pass more context (up to 15k chars) to allow the model to see the full form
    context_info = f"Current URL: {request.context_url}\n\n[Simplified Page DOM]\n{request.dom_snippet[:15000]}" if request.dom_snippet else f"Current URL: {request.context_url}"

    system_prompt = """You are an expert Microsoft 365 Admin Assistant, specializing in Graph Connectors and Search.
Your goal is to guide the admin through the configuration process **one step at a time**.

**CRITICAL INSTRUCTION**: 
- **Read the [Simplified Page DOM] carefully.** 
- **ONLY** provide guidance for the form fields, inputs, or errors that are **currently visible** on the screen.
- **DO NOT** provide a full tutorial. Do not list Step 1, Step 2, Step 3 if they are not visible.
- If the user is on the "Name" step, only talk about Name and Connection ID.
- If the user is on the "Authentication" step, only talk about Authentication.

**Logic to Follow**:
1. **Analyze**: Look for `<input>`, `<label>`, `<h1>`, or error messages in the DOM.
2. **identify Context**: Which specific step is the user on? (e.g., "Connection Settings", "Authentication", "Test Connection").
3. **Draft Advice**:
   - Suggest values for inputs (e.g., "For *Display Name*, enter 'Jira Production'").
   - Explain complex settings (e.g., "Enter the *Jira Service URL* carefully...").
   - If an error is visible, explain how to fix it.
4. **Style**: Be concise. Use Markdown. Use "You are here: [Step Name]" to orient the user.

If the DOM suggests the user just finished a step (e.g. clicked Next), confirm the previous action briefly and focus on the NEW fields.
"""

    # If this is a very specific field focus request, override the system prompt to be laser-focused
    # Also handle specific Action Commands like 'confirm the output matches' here to ensure they aren't missed
    msg_lower = user_msg.lower()
    
    triggers = ["field_focus", "focused on field", "confirm the output matches", "phase 1", "phase 2"]
    if any(t in msg_lower for t in triggers) or "action:" in msg_lower:
        print(f"DEBUG: Processing Field Focus or Special Action. Msg: {msg_lower[:100]}...")
        
        # 1. Specialized Logic for Display Name
        # CAUTION: 'Name' is ambiguous. It could be "Connection Name" (M365 Search) OR "Agent Name" (GCA Config).
        # We must disambiguate based on context.
        if ("display name" in msg_lower or "name" in msg_lower) and "phase 2" not in msg_lower:
             # Default to Connection Naming Logic (Old logic)
             print("DEBUG: >>> Display Name Logic Triggered <<<")
             today_str = datetime.date.today().strftime("%Y%m%d")
             
             # Extract tool name from user message if possible
             import re
             tool_name = "Tool"
             default_obj = "Items"
             
             # Combine context and current value for better detection
             combined_text = user_msg.lower()
             
             if "jira" in combined_text: 
                 tool_name = "Jira"
                 default_obj = "Tickets"
             elif "servicenow" in combined_text: 
                 tool_name = "ServiceNow"
                 default_obj = "Incidents"
             elif "oracle" in combined_text: 
                 tool_name = "Oracle"
                 default_obj = "DB"
             elif "azure devops" in combined_text or "ado" in combined_text: 
                 tool_name = "ADO"
                 default_obj = "WorkItems"
             elif "salesforce" in combined_text or "sfdc" in combined_text: 
                 tool_name = "SFDC"
                 default_obj = "Accounts"
             elif "confluence" in combined_text: 
                 tool_name = "Confluence"
                 default_obj = "Pages"
             elif "media" in combined_text:
                 tool_name = "MediaWiki"
                 default_obj = "Wikis"
             else:
                 # Fallback to regex extraction if no keywords found
                 match = re.search(r"Connector context:\s*'([^']+)'", user_msg)
                 if match:
                      tool_name = match.group(1).split(' ')[0].capitalize()

             print(f"DEBUG: Resolved Tool='{tool_name}', Obj='{default_obj}'")
             
             # Calculate the suggestion Deterministically
             final_suggestion = f"{tool_name} {default_obj} {today_str}"

             system_prompt = f"""You are a helpful assistant guiding a user to name their Graph Connector.
The user is focused on the 'Display Name' field.

**Key Insight**: The Display Name impacts search ranking.
- **Identity**: The name MUST start with **{tool_name}**.
- **Content**: It must describe the *objects* (e.g. {default_obj}), NOT the platform edition.

**CONSTRAINT**: 
1. Max Length: **30 chars**.
2. **MANDATORY SUGGESTION**: You MUST use exactly this value: "{final_suggestion}"

**Your Task**:
1. Insight: Explain why '{final_suggestion}' is the best choice (Identity + Content + Date). **Must insert a blank line between each numbered point for readability.**
2. Suggestion: Output the exact calculated string below within a text code block to prevent formatting issues.

**Output Format**:
**Insight**: 
[Point 1]

[Point 2]

[Point 3]
**Suggestion**: 
```text
{final_suggestion}
```
"""
        elif "description" in msg_lower:
             system_prompt = """You are a helpful assistant guiding a user to write a description for their Graph Connector.
The user is focused on the 'Description' field.

**Key Insight**: This field is the main source for semantic matching in the Skill Discovery layer.
- The description must clearly describe **WHAT** the data is (e.g., "Software development tasks") and **HOW** it helps (e.g., "Track bugs and features").
- Low-quality descriptions (e.g., "Test Connector") may cause the tool to be filtered out because the semantic distance > 0.6.

**Your Task**:
1. Warn against generic descriptions.
2. Provide a keyword-rich template.

**Output Format**:
**Insight**: [Warning and Strategy]
**Suggestion**: ```Contains all active [Tool] [Object], [Synonym1], and [Synonym2] for [Action].```
"""
        elif "confirm the output matches" in msg_lower:
             # Phase 1: Installation
             system_prompt = """You are a helpful assistant guiding the Graph Connector Agent installation.
The user has just confirmed their PowerShell Execution Policy is correct.

**Your Goal**: Guide the user through **Phase 1: Installation**.

**Response Structure**:
1. **Acknowledgment**: "Environment ready. Starting Phase 1."
2. **Steps**:
   - **Step 1**: Locate and double-click `GcaInstaller.msi`.
   - **Step 2**: Check **"I accept the terms..."** and click **Next**.
   - **Step 3**: Click **Install** and wait for completion.
   - **Step 4**: Click **Finish**.

3. **Confirmation**:
   - Ask the user to confirm installation is complete.
   - [I have installed the GCA](action:confirm-gca-phase1)

**Output Format**:
**Insight**: Starting Phase 1: Installation.
**Suggestion**: 
[Provide Steps 1-4 using Markdown lists. Insert a blank line between each step for readability.]
[End with the action button: [I have installed the GCA](action:confirm-gca-phase1)]
"""
        elif "phase 1" in msg_lower:
             # Phase 2: Registration Config
             system_prompt = """You are a helpful assistant guiding the Graph Connector Agent installation.
The user has confirmed the GCA is installed.

**Your Goal**: Guide the user through **Phase 2: Configuration**.

**Response Structure**:
1. **Acknowledgment**: "Great! Now let's register the agent."
2. **Steps**:
   1. Launch the **Graph connector agent config** app from the Start Menu.
   2. Click **Sign in** and log in with your **M365 Admin Account**.
   3. On the **Agent Details** screen:
     - **Name**: Enter `M365GCA`.
     - **App ID**: Pause here. You need to create this in Azure.

3. **Confirmation**:
   - Ask to proceed to Azure App creation.
   - [I am ready to create Azure App](action:confirm-gca-phase2)

**Output Format**:
**Insight**: Starting Phase 2: Configuration.
**Suggestion**: 
[Provide the steps using a numbered list (1., 2., 3.). Insert a blank line between each step for readability.]
[End with the action button: [I am ready to create Azure App](action:confirm-gca-phase2)]
"""
        elif "phase 2" in msg_lower:
             # Phase 3: Azure App Creation
             system_prompt = """You are a helpful assistant guiding the Graph Connector Agent installation.
The user is ready to create the Azure App.

**Your Goal**: Guide the user through **Phase 3: Azure App Registration**.

**Response Structure**:
1. **Acknowledgment**: "Let's create the Azure App to get the ID."
2. **Steps**:
   1. Go to [Azure Portal](https://azure.microsoft.com/) -> **Entra ID**.
     <br>
     ![Entra ID Icon](https://upload.wikimedia.org/wikipedia/commons/thumb/8/8c/Microsoft_Entra_ID_color_icon.svg/100px-Microsoft_Entra_ID_color_icon.svg.png)
   
   2. Click **+ Add** -> **App registration**.
     <br>
     ![Register App Form](https://learn.microsoft.com/en-us/entra/identity-platform/media/quickstart-register-app/portal-02-app-reg-01.png)

   3. Fill in the details:
     - Name: `M365GCAApp`
     - Click **Register**.

   4. Click **API permissions** -> **+ Add a permission**.

   5. Select **Microsoft Graph**.
     <br>
     ![Request API Permissions](chrome-extension://__MSG_@@extension_id__/images/Azure_API_Permission_1.jpg)

3. **Confirmation**:
   - Ask the user if they have reached the permissions screen.
   - [I have selected Microsoft Graph](action:confirm-gca-phase3)


**Output Format**:
**Insight**: Starting Phase 3: Azure App.
**Suggestion**: 
[Provide the steps using a numbered list (1., 2., 3.). Insert a blank line between each step for readability. Ensure images have their own lines.]
[End with the action button: [I have selected Microsoft Graph](action:confirm-gca-phase3)]
"""
        elif "confirm-gca-phase3" in msg_lower or "microsoft graph" in msg_lower:
             # Phase 3 Continued: Application Permissions
             system_prompt = """You are guiding the user through Azure App Registration (Phase 3).
The user has just selected "Microsoft Graph" in the API Permissions pane.

**Your Goal**: Guide the user to select the correct **Application permissions**.

**Response Structure**:
1. **Steps**:
   1. Click on **Application permissions** (NOT Delegated).
      <br>
      ![Select Application Permissions](chrome-extension://__MSG_@@extension_id__/images/Azure_API_Permission_2.jpg)

   2. In the search box, type `ExternalItem`.
   3. Expand **ExternalItem** and select **TWO** permissions:
      - `ExternalItem.ReadWrite.All`
      - `ExternalItem.ReadWrite.OwnedBy`
      <br>
      ![Select Permissions](chrome-extension://__MSG_@@extension_id__/images/Azure_API_Permission_3.jpg)

   4. Click **Add permissions** at the bottom.

2. **Confirmation**:
   - Ask the user to confirm they have added these 2 permissions.
   - [I have added the permissions](action:confirm-gca-phase3-done)

**Output Format**:
**Insight**: Selecting Application Permissions.
**Suggestion**: 
[Provide steps 1-4 using the images.]
"""
        elif "confirm-gca-phase3-done" in msg_lower or "user added application permission" in msg_lower:
             # Phase 3 Part 2: ExternalConnection Permission
             system_prompt = """You are guiding the user through Azure App Registration (Phase 3).
The user has added the 'ExternalItem' permissions.

**Your Goal**: Guide the user to add the **ExternalConnection** permission.

**Response Structure**:
1. **Steps**:
   1. Click **+ Add a permission** again.
   2. Select **Microsoft Graph** -> **Application permissions**.
   3. In the search box, type `ExternalConnection`.
   4. Expand **ExternalConnection** and select:
      - `ExternalConnection.ReadWrite.OwnedBy`
      <br>
      ![Select ExternalConnection](chrome-extension://__MSG_@@extension_id__/images/Azure_API_Permission_4.jpg)

   5. Click **Add permissions**.

2. **Confirmation**:
   - Ask the user to confirm they have added this permission.
   - [I have added ExternalConnection permission](action:confirm-gca-phase3-final)

**Output Format**:
**Insight**: Adding ExternalConnection Permission.
**Suggestion**: 
[Provide the steps clearly]
"""
        elif "confirm-gca-phase3-final" in msg_lower or "user added externalconnection permission" in msg_lower:
             # Phase 3 Part 3: Directory Permission
             system_prompt = """You are guiding the user through Azure App Registration (Phase 3).
The user has added the 'ExternalConnection' permissions.

**Your Goal**: Guide the user to add the **Directory** permission.

**Response Structure**:
1. **Steps**:
   1. Click **+ Add a permission** one last time.
   2. Select **Microsoft Graph** -> **Application permissions**.
   3. In the search box, type `Directory`.
   4. Expand **Directory** and select:
      - `Directory.Read.All`
      <br>
      ![Select Directory Permission](chrome-extension://__MSG_@@extension_id__/images/Azure_API_Permission_5.jpg)

   5. Click **Add permissions**.

2. **Confirmation**:
   - Ask the user to confirm they have added this permission.
   - [I have added Directory permission](action:confirm-gca-permissions-all)

**Output Format**:
**Insight**: Adding Directory Permission.
**Suggestion**: 
[Provide the steps 1-5 using the image.]
"""
        elif "confirm-gca-permissions-all" in msg_lower or "user added directory permission" in msg_lower:
             # Phase 3 Part 4: Admin Consent
             system_prompt = """You are guiding the user through Azure App Registration (Phase 3).
The user has added all necessary permissions.

**Your Goal**: Guide the user to **Grant Admin Consent**.

**Response Structure**:
1. **Steps**:
   1. Locate the button labeled **Grant admin consent for [Org Name]** (top of the list).
   2. Click it and select **Yes** in the popup.
      <br>
      ![Grant Admin Consent](chrome-extension://__MSG_@@extension_id__/images/Azure_API_Permission_6.jpg)

   3. **Verify**: Ensure all permissions now show a green checkmark under "Status".
      <br>
      ![Verify Consent](chrome-extension://__MSG_@@extension_id__/images/Azure_API_Permission_7.jpg)

2. **Confirmation**:
   - Ask the user to confirm the permissions are granted.
   - [I confirm admin consent is granted](action:confirm-gca-consent)

**Output Format**:
**Insight**: Granting Admin Consent.
**Suggestion**: 
[Provide the steps using the images.]
"""
        elif "confirm-gca-consent" in msg_lower or "user granted admin consent" in msg_lower:
             # Phase 3 Part 5: Certificates & secrets
             system_prompt = """You are guiding the user through Azure App Registration (Phase 3).
The user has granted Admin Consent.

**Your Goal**: Guide the user to **Certificates & secrets**.

**Response Structure**:
1. **Steps**:
   1. On the left menu, click **Certificates & secrets**.
      <br>
      ![Click Certificates & secrets](chrome-extension://__MSG_@@extension_id__/images/Azure_API_Permission_8.jpg)

2. **Confirmation**:
   - Ask the user to confirm they are on this page.
   - [I am on the Certificates & secrets page](action:confirm-gca-cert-page)

**Output Format**:
**Insight**: Navigating to Certificates & secrets.
**Suggestion**: 
[Provide steps using the image.]
"""
        elif "confirm-gca-cert-page" in msg_lower or "user opened certificates" in msg_lower:
             # Phase 3 Part 6: Create Secret
             system_prompt = """You are guiding the user through Azure App Registration (Phase 3).
The user is on the Certificates & secrets page.

**Your Goal**: Guide the user to create a **New client secret**.

**Response Structure**:
1. **Steps**:
   1. Click the **+ New client secret** button.
      <br>
      ![New Client Secret](chrome-extension://__MSG_@@extension_id__/images/Azure_API_Permission_9.jpg)

2. **Confirmation**:
   - Ask the user to confirm they have clicked the button and see the "Add a client secret" popup.
   - [I have clicked New client secret](action:confirm-gca-new-secret)

**Output Format**:
**Insight**: Creating Client Secret.
**Suggestion**: 
[Provide steps using the image.]
"""
        elif "confirm-gca-new-secret" in msg_lower or "user clicked new client secret" in msg_lower:
             # Phase 3 Part 7: Client Secret Details
             system_prompt = """You are guiding the user through Azure App Registration (Phase 3).
The user has clicked 'New client secret'.

**Your Goal**: Guide the user to fill in the secret details.

**Response Structure**:
1. **Steps**:
   1. In the **Description** field, enter `GCA Secret`.
   2. For **Expires**, select **Recommended: 6 months**.
   3. Click the **Add** button at the bottom.
      <br>
      ![Add Secret Details](chrome-extension://__MSG_@@extension_id__/images/Azure_API_Permission_10.jpg)

2. **Confirmation**:
   - Ask the user to confirm they have clicked Add.
   - [I have clicked Add](action:confirm-gca-secret-added)

**Output Format**:
**Insight**: Adding Client Secret.
**Suggestion**: 
[Provide steps using the image.]
"""
        elif "confirm-gca-secret-added" in msg_lower or "user added secret" in msg_lower:
             # Phase 3 Part 8: Copy Secret Value
             system_prompt = """You are guiding the user through Azure App Registration (Phase 3).
The secret has been created.

**Your Goal**: Guide the user to **IMMEDIATELY** copy the Secret Value.

**CRITICAL WARNING**:
- The **Value** will be hidden forever once you leave this page.
- You typically need the **Value** (not just the Secret ID) for configuration.

**Response Structure**:
1. **Steps**:
   1. Locate the **Value** column for the new secret.
   2. Copy the **Value** and save it securely (e.g., in a password manager).
      <br>
      ![Copy Secret Value](chrome-extension://__MSG_@@extension_id__/images/Azure_API_Permission_11.jpg)

2. **Confirmation**:
   - Ask the user to confirm they have recorded the Secret Value.
   - [I have recorded the Secret Value](action:confirm-gca-secret-value)

**Output Format**:
**Insight**: Copying Secret Value.
**Suggestion**: 
[Provide steps and the warning.]
"""
        elif "confirm-gca-secret-value" in msg_lower or "user recorded secret" in msg_lower:
             # Phase 3 Part 9: Get App ID
             system_prompt = """You are guiding the user through Azure App Registration (Phase 3).
The user has the Secret Value. Now we need the **Application (client) ID**.

**Your Goal**: Guide the user to get the App ID.

**Response Structure**:
1. **Steps**:
   1. On the left menu, click **Overview**.
   2. Locate the **Application (client) ID**.
   3. Copy it and save it alongside your Secret Value.
      <br>
      ![Copy App ID](chrome-extension://__MSG_@@extension_id__/images/Azure_API_Permission_12.jpg)

2. **Confirmation**:
   - Ask the user to confirm they have recorded the App ID.
   - [I have recorded the App ID](action:confirm-gca-final-appid)

**Output Format**:
**Insight**: Copying Application ID.
**Suggestion**: 
[Provide steps using the image.]
"""
        elif "confirm-gca-final-appid" in msg_lower or "user recorded app id" in msg_lower:
             # Phase 4 Start: Register in Config App
             system_prompt = """You are guiding the user through Graph Connector Agent installation.
**Phase 3 (Azure Setup) is COMPLETE.**

**Your Goal**: Guide the user to register the agent in the configuration app.

**Response Structure**:
1. **Steps**:
   1. Switch back to the **Graph connector agent config** window on your desktop.
   2. Paste your **Application ID**.
   3. Paste your **client secret** (Value).
   4. Click **Register**.
      <br>
      ![Register Agent](chrome-extension://__MSG_@@extension_id__/images/Azure_API_Permission_13.jpg)

2. **Confirmation**:
   - Ask the user to confirm they have clicked Register.
   - [I have clicked Register](action:confirm-gca-register-clicked)

**Output Format**:
**Insight**: Azure Setup Complete. Back to Agent Config.
**Suggestion**: 
[Provide steps.]
"""
        elif "confirm-gca-register-clicked" in msg_lower or "user clicked register" in msg_lower:
             # Phase 4 Next: Health Check
             system_prompt = """You are guiding the user through Graph Connector Agent installation.
You have clicked Register.

**Your Goal**: Guide the user to perform a Health Check.

**Response Structure**:
1. **Steps**:
   1. Locate the **Health Check** button in the app window.
   2. Click it to verify the agent status.
      <br>
      ![Click Health Check](chrome-extension://__MSG_@@extension_id__/images/Azure_API_Permission_14.jpg)

2. **Confirmation**:
   - Ask the user to confirm they have clicked the button.
   - [I have clicked Health Check](action:confirm-gca-health-check-clicked)

**Output Format**:
**Insight**: verifying Agent Health.
**Suggestion**: 
[Provide steps.]
"""
        elif "confirm-gca-health-check-clicked" in msg_lower or "user clicked health check" in msg_lower:
             # Phase 4 Next: Verify Success
             system_prompt = """You are guiding the user through Graph Connector Agent installation.
**Health Check Initiated.**

**Your Goal**: Confirm the agent is healthy.

**Response Structure**:
1. **Steps**:
   1. Wait for the check to complete (it may take a moment).
   2. Verify that you see a green **Success** banner indicating the agent connection is successful.
      <br>
      ![Success Banner](chrome-extension://__MSG_@@extension_id__/images/Azure_API_Permission_15.jpg)

2. **Confirmation**:
   - Ask the user to confirm they see the success message.
   - [I see the Success banner](action:confirm-gca-health-success)

**Output Format**:
**Insight**: Verifying Health Success.
**Suggestion**: 
[Provide steps.]
"""
        elif "confirm-gca-health-success" in msg_lower or "user confirmed success" in msg_lower:
             # Phase 5: Create Connection & Select GCA
             system_prompt = """You are guiding the user through Graph Connector Agent installation.
**Agent is Healthy.**

**Your Goal**: Guide the user to select the agent in the browser.

**Response Structure**:
1. **Steps**:
   1. Return to the browser tab where you are creating the connection.
   2. Refresh the page if needed.
   3. In the **GCA (Graph Connector Agent)** dropdown list, you should now see your newly registered agent.
   4. Select the agent from the list.
      <br>
      ![Select GCA](chrome-extension://__MSG_@@extension_id__/images/GCA_selection_1.jpg)

**Note**: If the GCA option was already present previously, the setup steps were skipped.

2. **Confirmation**:
   - Ask the user to confirm they have selected the agent.
   - [I have selected the GCA](action:confirm-gca-selected)

**Output Format**:
**Insight**: Selecting the Agent.
**Suggestion**: 
[Provide steps.]
"""
        elif "confirm-gca-selected" in msg_lower or "user selected gca" in msg_lower:
             # Transition to Authentication
             system_prompt = """You are guiding the user through the "Authentication" phase for Jira Data Center.
The user has selected the GCA and is now facing the Authentication form.

**Your Goal**: Guide the user to configure OAuth 2.0.

**Response Structure**:
1. **Insight**: "Next Step: Authentication."
2. **Suggestion**:
   "Click **Next** to proceed to the Authentication step."
   <br>
   "Select **OAuth 2.0** as the type."
   <br><br>
   "You will need a **Client ID** and **Client Secret** which you must generate in your Jira Administration Console."
   <br><br>
   **Do you have these credentials?**
   <br>
   · If **NO**, I can guide you to generate them in Jira:
   [Guide: Get Jira Credentials](action:guide-jira-oauth)
   <br>
   · If **YES**, enter them and click Authorize:
   [I have authorized the connection](action:confirm-auth-success)

**Output Format**:
**Insight**: Moving to Authentication.
**Suggestion**: 
[Content]
"""
        elif "guide-jira-oauth" in msg_lower:
             # Jira OAuth Guide
             system_prompt = """You are an expert Jira Administrator.
The user needs to generate Client ID and Secret for the Microsoft Graph Connector.

**Your Goal**: Provide the EXACT steps to create an Application Link in Jira Data Center.

**Response Structure**:
1. **Insight**: "Generating OAuth 2.0 Credentials."
2. **Suggestion**:
   "**Step 1**: Log in to Jira as Admin."
   <br><br>
   "**Step 2**: Go to **Settings (gear)** -> **Applications** -> **Application links**."
   <br><br>
   "**Step 3**: Click **Create link**."
   <br><br>
   "**Step 4**: Enter the application's URL (e.g., `https://gcs.office.com`) and click **Create new link**."
   <br><br>
   "**Step 5**: In the **Configure Application Links** window, fill in the required details:"
   <br>
   - **Application Name**: Microsoft Graph Connector
   - **Application Type**: Generic Application
   - **Redirect URL**: `https://gcs.office.com/v1.0/admin/oauth/callback`
   - **Permission**: Select `Write`
   <br><br>
   "**Step 6**: Click **Save**."
   <br><br>
   "**Step 7**: Once the application link is created, click on the pencil icon (Edit) to open its details."
   <br><br>
   "**Step 8**: Under the **Incoming Authentication** (or OAuth) section, generate the **Client ID** and **Client Secret**."
   <br><br>
   "**Step 9**: Copy and securely save the **Client ID** and **Client Secret**, as they will be required here."
   <br><br>
   [I have my Client ID & Secret](action:confirm-jira-oauth-done)

**Output Format**:
**Insight**: Jira Admin Steps.
**Suggestion**: 
[Steps]
"""

        elif "confirm-jira-oauth-done" in msg_lower:
             system_prompt = """You are a helpful assistant.
The user has just generated their OAuth credentials.

**Response Structure**:
1. **Insight**: "Credentials Ready."
2. **Suggestion**:
   "Excellent. Please paste the **Client ID** and **Client Secret** into the fields below, then click **Authorize**."
"""

        elif "confirm-auth-success" in msg_lower:
             # Authentication Done
             system_prompt = """You are guiding the user.
The user has clicked Authorize and presumably succeeded.

**Your Goal**: Move to the next step (Data Selection).

**Response Structure**:
1. **Insight**: "Authentication Complete."
2. **Suggestion**:
   "Great! Now that you are authenticated, click **Next**."
   <br>
   "We will now select which Jira projects or issues you want to index."
"""
        elif "start-gca-install-guide" in msg_lower:
             # Explicitly start the installation guide (Phase 1)
             system_prompt = """You are a helpful assistant guiding the Graph Connector Agent installation.
**Phase 1: Installation**.

**Response Structure**:
1. **Acknowledgment**: "Starting GCA Installation Guide."
2. **Steps**:
   1. Download the agent: [Download Graph Connector Agent](https://aka.ms/gca).
   <br>
   2. Open PowerShell: [Open PowerShell](action:open-powershell).
   <br>
   3. Run the following commands to set execution policies:
   ```powershell
   Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
   ```
   ```powershell
   Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope LocalMachine
   ```
   Check the policies:
   ```powershell
   Get-ExecutionPolicy -List
   ```
   <br>
   4. Check the results against this table:
   | Scope | ExecutionPolicy |
   | :--- | :--- |
   | CurrentUser | RemoteSigned |
   | LocalMachine | RemoteSigned |
   <br>
   5. Confirm the installation is complete:
   <br>
   [I have installed the GCA](action:confirm-gca-phase1)

**Output Format**:
**Insight**: Phase 1: Installation.
**Suggestion**: 
[Provide steps.]
"""

        elif "authentication type" in msg_lower or "client id" in msg_lower or "client secret" in msg_lower:
             # Authentication / OAuth Guidance
             system_prompt = """You are an expert on Jira Data Center OAuth setup.
The user is focusing on the 'Authentication type', 'Client ID', or 'Client Secret' field.
**OAuth 2.0** is the standard (and only) option here.
The user primarily needs the **Client ID** and **Client Secret**, which requires setting up an 'Application Link' in Jira.

**Response Structure**:
1. **Insight**: "OAuth 2.0 Configuration Required."
2. **Suggestion**:
   "OAuth 2.0 is the required method. To fill the **Client ID** and **Client Secret** below, you must create an incoming Application Link in your Jira Data Center."
   <br><br>
   **Click below for step-by-step instructions (Redirect URI included):**
   <br>
   [Show Configuration Guide](action:guide-jira-oauth)
"""

        elif "graph connector agent" in msg_lower or "agent" in msg_lower:
             # Decision logic based on DOM content
             system_prompt = """You are an expert on the 'Microsoft Graph Connector Agent'.
The user is focusing on the 'Graph Connector Agent' dropdown field.

**CRITICAL PRIORITY**: 
First, check the `current value` mentioned in the user's message.
- If `current value` is something like "GCA315", "Agent-01", or any specific name (and NOT empty, NOT "Select...", NOT "Loading..."), **STOP ANALYZING DOM**. The user has selected an agent. **Use Scenario A**.

**Detailed DOM Analysis (Only if 'current value' is empty/default)**:
Look at the `[Simplified Page DOM]` context provided.
**CRITICAL**: Fluent UI dropdowns are NOT standard `<select>` tags. 
You must look for:
1. `div` or `span` elements with `role="option"`.
2. Content inside classes like `ms-Callout`, `ms-Layer`, or `ms-List`.
3. Any text that looks like a custom Agent Name (e.g., "GCA...", "Agent", "Machine1").

**Scenarios**:

1. **Scenario A: Agent Already Selected**
   - **Condition**: The input field `current value` displays a selected agent name (e.g., "GCA315") OR the DOM shows the selected item prominently.
   - **Goal**: Validate the selection has been made.
   - **Response Structure**:
     1. **Insight**: "GCA Selected."
     2. **Suggestion**: "You have already selected an agent ({current_value}). You can proceed to the next step."

2. **Scenario B: Options Exist (Agent is installed)**
   - **Condition**: You see ANY text indicating a specific agent (e.g. "GCA315", "MyAgent", "Desktop-XYZ") in the DOM, especially in a list/portal area.
   - **Goal**: Inform them the agent is detected and guide selection.
   - **Response Structure**:
     1. **Insight**: "GCA Installed and Registered."
     2. **Suggestion**:
        "You have successfully installed and registered the GCA. Please select the correct GCA from the list."
        <br>
        ![Select GCA](chrome-extension://__MSG_@@extension_id__/images/GCA_selection_1.jpg)
        <br><br>
        "If you want to understand the GCA installation and registration process, click below:"
        <br>
        [Guide me to install GCA](action:start-gca-install-guide)

3. **Scenario C: Dropdown is Empty (No Options)**
   - **Condition**: The dropdown list only contains "Select...", "Loading...", or no list is visible using a DOM scan.
   - **Goal**: Guide them to install using the standard Phase 1 instructions, BUT acknowledge the possibility of a "closed list".
   - **Response Structure**:
     1. **Insight**: "No Agent Detected (or List Closed)."
     2. **Suggestion**:
        "I currently cannot see any agents. This might happen if the dropdown list is closed."
        
        **If you see your agent in the list**, please select it directly and confirm:
        [I have selected the GCA](action:confirm-gca-selected)
        
        **If the list is truly empty**, please install the agent:
        
        1. Download the agent: [Download Graph Connector Agent](https://aka.ms/gca).
        2. Open PowerShell: [Open PowerShell](action:open-powershell).
        3. Run the following commands to set execution policies:
        ```powershell
        Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
        ```
        ```powershell
        Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope LocalMachine
        ```
        Check the policies:
        ```powershell
        Get-ExecutionPolicy -List
        ```
        4. Check the results against this table:
        | Scope | ExecutionPolicy |
        | :--- | :--- |
        | CurrentUser | RemoteSigned |
        | LocalMachine | RemoteSigned |
        
        5. Confirm the installation is complete:
        [I have installed the GCA](action:confirm-gca-phase1)

**Output Format**:
**Insight**: [Status Keyword]
**Suggestion**: 
[Rich Content with Steps, Code Blocks, and Buttons]

**Constraint**: Do NOT output any analysis or thought process. ONLY output the **Insight** and **Suggestion** sections.
"""
        else:
             # 3. General Field Logic
             system_prompt = """You are a real-time form filling assistant. 
The user currently has their cursor inside a specific input field.

**Your Task**:
1. Explain what this specific field is for (Insight).
2. Provide a valid example value (Suggestion).
3. Mention any critical warnings.

**Output Format**:
**Insight**: [What is this field and any warnings]
**Suggestion**: ```[Example Value]```
"""

    try:
        completion = await state.ai_client.chat.completions.create(
            model=os.getenv("AZURE_OPENAI_DEPLOYMENT_NAME", "gpt-4o"),
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "system", "content": f"[Context]\n{context_info}"},
                {"role": "user", "content": user_msg}
            ],
            temperature=0.7,
            max_tokens=2000
        )
        
        return {"response": completion.choices[0].message.content}
        
    except Exception as e:
        logger.error(f"AI Error: {e}")
        return {"response": f"🤖 **AI Error:** I encountered an issue connecting to my brain.\n\n`{str(e)}`"}

@app.get("/me")
async def get_me():
    # Strict check: Do not attempt to use the client unless authentication is fully completed.
    # This leads to "screen flashing" in the terminal if the frontend polls this endpoint while 
    # DeviceCodeCredential is still waiting for user input.
    if not state.is_authenticated or not state.client:
        raise HTTPException(status_code=401, detail="Not authenticated yet.")
    
    try:
        user = await state.client.me.get()
        return {"displayName": user.display_name, "mail": user.mail, "id": user.id}
    except Exception as e:
        # In case of token expiry or other graph errors
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/tools/open-powershell")
async def open_powershell():
    """
    Opens a standalone PowerShell window on the host machine.
    """
    try:
        import subprocess
        # 'start' is a Windows shell command to open a new window
        subprocess.Popen(["start", "powershell"], shell=True)
        return {"status": "success", "message": "PowerShell launched"}
    except Exception as e:
        logger.error(f"Failed to launch PowerShell: {e}")
        return {"status": "error", "message": str(e)}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)
