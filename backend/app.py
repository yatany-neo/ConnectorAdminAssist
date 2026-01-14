import asyncio
import logging
import os
import datetime
from contextlib import asynccontextmanager
from dotenv import load_dotenv
from openai import AsyncAzureOpenAI
from pydantic import BaseModel
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from azure.identity import DeviceCodeCredential
from azure.core.credentials import AccessToken
from msgraph import GraphServiceClient

# Load environment variables
load_dotenv()

# Configure logging to see the device code
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("m365-agent-backend")

class AgentState:
    def __init__(self):
        self.client = None
        self.credential = None
        self.is_authenticated = False
        self.auth_code_info = None
        self.auth_task = None
        self.ai_client = None # Azure OpenAI Client

state = AgentState()

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    logger.info("Backend Service Starting...")
    
    # Initialize Azure OpenAI Client
    try:
        if os.getenv("AZURE_OPENAI_API_KEY"):
            state.ai_client = AsyncAzureOpenAI(
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
    # Shutdown logic if needed
    logger.info("Backend Service Shutting Down...")

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
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Global state
class AgentState:
    def __init__(self):
        self.client = None
        self.credential = None
        self.is_authenticated = False
        self.auth_code_info = None  # To store the device code info
        self.auth_task = None       # To store the background task

state = AgentState()

@app.on_event("startup")
async def startup_event():
    logger.info("Backend Service Started. (Legacy Handler)")

def device_code_callback(verification_uri, user_code, expires_on):
    """
    Callback to capture the device code and URI instead of just printing to stdout.
    """
    logger.info(f"CAPTURED DEVICE CODE: {user_code}")
    # Fix: Ensure no None values are passed to frontend to avoid 'undefined'
    safe_uri = verification_uri if verification_uri else "https://microsoft.com/devicelogin"
    safe_code = user_code if user_code else "UNKNOWN_CODE"

    state.auth_code_info = {
        "verification_uri": safe_uri,
        "user_code": safe_code,
        "expires_on": expires_on.isoformat() if hasattr(expires_on, 'isoformat') else str(expires_on),
        "message": "Please sign in to authenticate."
    }

async def perform_login():
    """
    Background task to wait for user login.
    """
    try:
        # Trigger the auth flow by making a call
        await state.client.me.get()
        state.is_authenticated = True
        state.auth_code_info = None # Clear code after success
        logger.info("Authentication Background Task Completed Successfully.")
    except Exception as e:
        logger.error(f"Authentication Background Task Failed: {e}")
        state.auth_code_info = {"error": str(e)}

@app.get("/")
def read_root():
    return {
        "status": "running", 
        "service": "M365 Admin Companion Backend",
        "auth_status": "authenticated" if state.is_authenticated else "unauthenticated"
    }

@app.post("/auth/login")
async def login():
    """
    Initiates the Device Code Flow in the BACKGROUND.
    """
    global state
    if state.is_authenticated:
         return {"status": "success", "message": "Already authenticated"}

    # Reset state
    state.auth_code_info = None
    
    # Configure Credential with Callback (using Async Wrapper)
    # prompt_callback allows us to capture the code
    # We pass arguments to the underlying SyncDeviceCodeCredential
    
    # User 'Microsoft Graph PowerShell' Client ID as it has broad Graph permissions
    # including ExternalConnection.ReadWrite.OwnedBy
    client_id = "14d82eec-204b-4c2f-b7e8-296a70dab67e" 
    
    state.credential = AsyncDeviceCodeCredential(
        client_id=client_id,
        prompt_callback=device_code_callback
    )
    
    scopes = ["https://graph.microsoft.com/ExternalConnection.ReadWrite.OwnedBy", "https://graph.microsoft.com/User.Read"]
    state.client = GraphServiceClient(credentials=state.credential, scopes=scopes)
    
    # Start the actual login process in the background so we don't block the HTTP response
    state.auth_task = asyncio.create_task(perform_login())

    return {
        "status": "pending_interaction", 
        "details": "Authentication started in background.", 
        "instruction": "Poll /auth/code to get the device code."
    }

@app.get("/auth/code")
def get_auth_code():
    """
    Returns the current device code info for the frontend to display.
    """
    if state.is_authenticated:
        return {"status": "authenticated"}
    
    if state.auth_code_info:
        return {"status": "present", **state.auth_code_info}
            
    return {"status": "waiting", "message": "Waiting for device code generation..."}


from pydantic import BaseModel

class ChatRequest(BaseModel):
    message: str
    context_url: str = ""
    dom_snippet: str = "" # New field for page content

@app.post("/agent/chat")
async def chat(request: ChatRequest):
    """
    Intelligent Chat using Azure OpenAI GPT-4o.
    """
    # If AI is not configured, fallback to rule-based or error
    if not state.ai_client:
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
    if "field_focus" in user_msg or (request.message and "focused on field" in request.message):
        # Normalize msg for checking
        msg_lower = user_msg.lower()
        print(f"DEBUG: Processing Field Focus. Msg: {msg_lower[:100]}...")
        
        # 1. Specialized Logic for Display Name
        if "display name" in msg_lower or "name" in msg_lower:
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
        elif "graph connector agent" in msg_lower or "agent" in msg_lower:
             # Specialized logic for the Agent interactions (Install Guide OR Selection)
             system_prompt = """You are an expert on the 'Microsoft Graph Connector Agent'.
The user is focusing on the 'Graph Connector Agent' dropdown field.

**Scenarios & Responses**:

1. **If the dropdown is EMPTY (No options):**
   - The user needs to install the agent locally.
   
   - **Step 1**: Provide the download link: [Download Graph Connector Agent](https://aka.ms/gca).
     (Do not use horizontal rules '---' between steps).

   - **Step 2**: Provide the **3 Mandatory PowerShell Commands** (in separate code blocks). 
     First, provide the shortcut link: [Open PowerShell](action:open-powershell).
     Then, list the commands:
     ```powershell
     Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
     ```
     ```powershell
     Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope LocalMachine
     ```
     ```powershell
     Get-ExecutionPolicy -List
     ```

   - **Step 3**: Verify Policy
     Ask the admin to confirm the output of `Get-ExecutionPolicy -List` matches this table EXACTLY (Render as Markdown Table):
     | Scope | ExecutionPolicy |
     | :--- | :--- |
     | CurrentUser | RemoteSigned |
     | LocalMachine | RemoteSigned |
     
     <br>
     <br>

   - **Step 4**: **Confirm & Proceed**
     State clearly: "If your output matches the table above, your environment is correctly configured. You can now proceed with the Graph Connector Agent installation/registration wizard."

2. **If the dropdown has options:**
   - Advise the user to simply select their registered agent.

**Output Format**:
**Insight**: [Explain that they need to select an active agent, or install one if none exist.]
**Suggestion**: [Follow the numbered steps above. Ensure there are blank lines between steps for readability.]
"""
        elif "search" in msg_lower or ("url" in msg_lower and "jira" in msg_lower): 
             # Explicitly IGNORE Search Boxes and Jira URL fields as per user request
             return {"response": ""}
             return {"response": ""}
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
            max_tokens=600
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
