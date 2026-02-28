// const API_URL = 'http://localhost:8000';
const API_URL = 'https://connector-backend-neo-6605.azurewebsites.net';

const ui = {
    overlay: document.getElementById('login-overlay'),
    btnLogin: document.getElementById('btn-login'),
    loginStatus: document.getElementById('login-status'),
    backendDot: document.getElementById('backend-status-indicator'),
    connectionDot: document.getElementById('connection-dot'),
    connectionText: document.getElementById('connection-text'),
    userDisplay: document.getElementById('user-display'),
    chatContainer: document.getElementById('chat-container')
};

let isAuthenticated = false;
let sessionID = null;

// Initialize Session ID
async function initSession() {
    let stored = await chrome.storage.local.get(['session_id']);
    if (stored.session_id) {
        sessionID = stored.session_id;
    } else {
        sessionID = crypto.randomUUID();
        await chrome.storage.local.set({ session_id: sessionID });
    }
    console.log("Session ID:", sessionID);
}

// Helper to add headers
function getHeaders() {
    return {
        'Content-Type': 'application/json',
        'X-Session-ID': sessionID
    };
}

// 1. Connectivity Check Loop
async function checkBackend() {
    if (!sessionID) await initSession();

    try {
        const response = await fetch(`${API_URL}/`, { headers: getHeaders() });
        const data = await response.json();
        
        if (data.status === 'running') {
            ui.backendDot.className = 'status-indicator status-green';
            if (!isAuthenticated) {
                ui.btnLogin.disabled = false;
                if(ui.loginStatus.textContent.includes("Cannot reach"))
                    ui.loginStatus.textContent = "Backend ready. Please sign in.";
            }
            
            if (data.auth_status === 'authenticated' && !isAuthenticated) {
                // Check if we can get user info
                await fetchUserInfo();
            }
        }
    } catch (e) {
        ui.backendDot.className = 'status-indicator status-red';
        ui.btnLogin.disabled = true;
        if (!isAuthenticated)
            ui.loginStatus.textContent = "Connecting to Backend...";
        // console.error("Backend unavailable", e);
    }
}

// 2. Login Flow
ui.btnLogin.addEventListener('click', async () => {
    ui.loginStatus.textContent = "Initializing Login... Please wait.";
    ui.loginStatus.style.color = "#0078D4";
    
    try {
        const res = await fetch(`${API_URL}/auth/login`, { 
            method: 'POST',
            headers: getHeaders()
        });
        const data = await res.json();
        
        if (data.status === 'pending_interaction') {
             ui.loginStatus.innerHTML = `Starting authentication flow...`;
             // Start polling for the code
             startAuthCodePolling();
        } else if (data.status === 'success') {
             // Already authenticated?
             fetchUserInfo();
        }
    } catch (e) {
        ui.loginStatus.textContent = "Error initiating login.";
    }
});

let authPollInterval;

function startAuthCodePolling() {
    if (authPollInterval) clearInterval(authPollInterval);
    authPollInterval = setInterval(async () => {
        try {
            // 1. Check for the Code
            const res = await fetch(`${API_URL}/auth/code`, { headers: getHeaders() });
            const data = await res.json();

            if (data.status === 'present') {
                // Check if we are already displaying this exact code to avoid refreshing DOM and killing selection
                const currentCodeDisplayed = ui.loginStatus.innerText.includes(data.user_code);
                
                if (!currentCodeDisplayed) {
                    // Show the code to the user!
                    ui.loginStatus.innerHTML = `
                        <strong>Sign-In Required:</strong><br>
                        1. Go to: <a href="${data.verification_uri}" target="_blank">${data.verification_uri}</a><br>
                        2. Enter Code: <span style="font-size: 1.2em; font-weight: bold; padding: 2px 5px; background: #eee; user-select: all;">${data.user_code}</span><br>
                        <small>Waiting for you to complete sign-in...</small>
                    `;
                }
            } else if (data.status === 'authenticated') {
                 // Success!
                 clearInterval(authPollInterval);
                 fetchUserInfo();
            } else if (data.status === 'waiting') {
                // Still waiting for code generation
                ui.loginStatus.textContent = "Connecting to Microsoft Identity...";
            }
        } catch(e) {
             // Backend down?
        }
    }, 2000);
}

function startAuthPolling() {
    // Deprecated in favor of startAuthCodePolling which handles both phases
}

async function fetchUserInfo() {
    try {
        const res = await fetch(`${API_URL}/me`, { headers: getHeaders() });
        if (res.ok) {
            const user = await res.json();
            completeLogin(user.displayName);
            return true;
        }
    } catch (e) {}
    return false;
}

function completeLogin(userName) {
    isAuthenticated = true;
    ui.overlay.classList.add('hidden');
    ui.connectionDot.className = 'status-indicator status-green';
    ui.connectionText.textContent = "Securely Connected";
    ui.userDisplay.textContent = userName;
    addMessage('system', `Authentication succeeded. I will guide you to create connections.`);
    
    // Check initial context
    if (currentContextUrl) {
         askAgent("Context Update", currentContextUrl);
    }
}

// 4. Context Awareness
let currentContextUrl = "";
let pendingIntent = null; // Store intent from user clicks

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
    // 4.1 Handle Intent (User Clicks) - FAST PATH
    if (message.type === 'USER_INTENT') {
        const intent = message.payload;
        console.log("Intent Received:", intent);
        
        // Store for backup
        pendingIntent = intent;

        // TRIGGER IMMEDIATELY if authenticated
        if (isAuthenticated) {
            sendResponse({status: "received"}); // Acknowledge receipt
            const now = Date.now();
            
            // Special handling for Field Focus (Debounce needed!)
            if (intent.action === 'field_focus') {
                if (window.focusDebounce) clearTimeout(window.focusDebounce);
                window.focusDebounce = setTimeout(() => {
                    // DEDUPLICATION: Removed long cooldown (20s) to allow highlighting on re-focus
                    // The askAgent function handles skipping API calls if history exists.
                    
                    window.lastAskedLabel = intent.field_label;
                    window.lastAskedTime = Date.now();

                    // Only ask if user dwells on a field for >0.8 seconds
                    askAgent("Field Guidance", "field-focus", intent); 
                }, 800);
                return;
            }

            // Check if we haven't already responded to this exact timestamp recently
            if (!window.lastIntentTime || (now - window.lastIntentTime > 2000)) {
                window.lastIntentTime = now;
                askAgent("Intent Guided Help", "intent-trigger", intent);
            }
        }
    }

    // 4.2 Handle Navigation (Page Loads) - SLOW PATH / CONTEXT UPDATE
    if (message.type === 'CONTEXT_UPDATE') {
        currentContextUrl = message.payload.url;
        
        // If we successfully identified a header title that implies a connector
        // We can use that as a fallback if the click was missed
        if (isAuthenticated && message.payload.page_header) {
             const header = message.payload.page_header;
             // Only auto-trigger if we are relatively "idle" (no pending click intent handling)
             if (!window.lastIntentTime || (Date.now() - window.lastIntentTime > 5000)) {
                 if (header.includes("Jira") || header.includes("ServiceNow") || header.includes("Oracle")) {
                     askAgent("Intent Guided Help", currentContextUrl, { connector: header });
                 }
             }
        }
    }
});

// 3. Chat logic
// Add Global Click Listener for Action Buttons
document.addEventListener('click', async (e) => {
    // 1. Copy Button Logic
    if (e.target.classList.contains('copy-btn')) {
        const rawCode = decodeURIComponent(e.target.getAttribute('data-code'));
        navigator.clipboard.writeText(rawCode).then(() => {
            const originalText = e.target.textContent;
            e.target.textContent = "COPIED!";
            setTimeout(() => e.target.textContent = originalText, 2000);
        });
    }
    // 2. Action Button Logic (Backend Tools)
    const actionBtn = e.target.closest('.action-btn');
    if (actionBtn) {
        const toolAction = actionBtn.getAttribute('data-action') ? actionBtn.getAttribute('data-action').trim() : "";
        console.log("[M365 Agent] Action clicked:", toolAction);

        if (toolAction === 'open-powershell') {
            try {
                actionBtn.textContent = "🚀 Launching...";
                actionBtn.disabled = true;
                const res = await fetch(`${API_URL}/tools/open-powershell`, { 
                    method: 'POST',
                    headers: getHeaders() 
                });
                const json = await res.json();
                if (json.status === 'success') {
                     actionBtn.textContent = "✅ Opened";
                } else {
                     actionBtn.textContent = "❌ Failed";
                     addMessage('system', 'Error launching tool: ' + json.message);
                }
                setTimeout(() => {
                     actionBtn.textContent = "🚀 Open PowerShell";
                     actionBtn.disabled = false;
                }, 3000);
            } catch(err) {
                console.error(err);
                actionBtn.textContent = "❌ Network Error";
            }
        }
        
        // New Action: Confirm GCA Install (Sends text to agent)
        if (toolAction === 'confirm-gca-install') {
             actionBtn.textContent = "✅ Confirmed";
             actionBtn.disabled = true;
             askAgent("I confirm the output matches the table.", currentContextUrl);
        }

        // New Manual Trigger for Install Guide
        if (toolAction === 'start-gca-install-guide') {
            actionBtn.textContent = "🚀 Starting Guide...";
            actionBtn.disabled = true;
            askAgent("Action: start-gca-install-guide", currentContextUrl);
        }

        // New Manual Trigger for Jira OAuth Guide
        if (toolAction === 'guide-jira-oauth') {
            actionBtn.textContent = "🚀 Opening Guide...";
            actionBtn.disabled = true;
            askAgent("Action: guide-jira-oauth", currentContextUrl);
        }

        // Phase 1 Confirmation: Installed MSI
        if (toolAction === 'confirm-gca-phase1') {
             actionBtn.textContent = "✅ Installed";
             actionBtn.disabled = true;
             askAgent("Action: User completed Phase 1 (Installation)", currentContextUrl);
        }

        // Phase 2 Confirmation: Launched App
        if (toolAction === 'confirm-gca-phase2') {
             actionBtn.textContent = "✅ App Launched";
             actionBtn.disabled = true;
             askAgent("Action: User completed Phase 2 (Config App Launch)", currentContextUrl);
        }

        // Phase 3 Confirmation: Selected MS Graph
        if (toolAction === 'confirm-gca-phase3') {
             actionBtn.textContent = "✅ Selected";
             actionBtn.disabled = true;
             askAgent("Action: User selected Microsoft Graph", currentContextUrl);
        }

        // Phase 3 Done Confirmation: Added Permission
        if (toolAction === 'confirm-gca-phase3-done') {
             actionBtn.textContent = "✅ Permission Added";
             actionBtn.disabled = true;
             askAgent("Action: User added Application Permission", currentContextUrl);
        }

        // Phase 3 Final Confirmation: Added ExternalConnection
        if (toolAction === 'confirm-gca-phase3-final') {
             actionBtn.textContent = "✅ ExternalConnection Added";
             actionBtn.disabled = true;
             askAgent("Action: User added ExternalConnection Permission", currentContextUrl);
        }

        // Phase 3 Permissions Complete
        if (toolAction === 'confirm-gca-permissions-all') {
             actionBtn.textContent = "✅ Permissions Complete";
             actionBtn.disabled = true;
             askAgent("Action: User added Directory Permission", currentContextUrl);
        }

        // Phase 3 Consent Complete
        if (toolAction === 'confirm-gca-consent') {
             actionBtn.textContent = "✅ Consent Granted";
             actionBtn.disabled = true;
             askAgent("Action: User granted admin consent", currentContextUrl);
        }

        // Phase 3 App ID Copied
        if (toolAction === 'confirm-gca-appid') {
             actionBtn.textContent = "✅ ID Copied";
             actionBtn.disabled = true;
             askAgent("Action: User copied App ID", currentContextUrl);
        }

        // Phase 3 Certificates Page
        if (toolAction === 'confirm-gca-cert-page') {
             actionBtn.textContent = "✅ Page Opened";
             actionBtn.disabled = true;
             askAgent("Action: User opened Certificates & secrets page", currentContextUrl);
        }

        // Phase 3 New Secret Clicked
        if (toolAction === 'confirm-gca-new-secret') {
             actionBtn.textContent = "✅ Button Clicked";
             actionBtn.disabled = true;
             askAgent("Action: User clicked New client secret", currentContextUrl);
        }

        // Phase 3 Secret Added
        if (toolAction === 'confirm-gca-secret-added') {
             actionBtn.textContent = "✅ Secret Added";
             actionBtn.disabled = true;
             askAgent("Action: User added secret", currentContextUrl);
        }

        // Phase 3 Secret Copied
        if (toolAction === 'confirm-gca-secret-value') {
             actionBtn.textContent = "✅ Secret Recorded";
             actionBtn.disabled = true;
             askAgent("Action: User recorded secret", currentContextUrl);
        }

        // Phase 3 Final: App ID Recorded
        if (toolAction === 'confirm-gca-final-appid') {
             actionBtn.textContent = "✅ App ID Recorded";
             actionBtn.disabled = true;
             askAgent("Action: User recorded App ID", currentContextUrl);
        }

        // Phase 4: Register Clicked
        if (toolAction === 'confirm-gca-register-clicked') {
            actionBtn.textContent = "✅ Register Clicked";
            actionBtn.disabled = true;
            askAgent("Action: User clicked Register", currentContextUrl);
        }

        // Phase 4: Health Check Clicked
        if (toolAction === 'confirm-gca-health-check-clicked') {
            actionBtn.textContent = "✅ Health Check Clicked";
            actionBtn.disabled = true;
            askAgent("Action: User clicked Health Check", currentContextUrl);
        }

        // Phase 4: Health Check Success
        if (toolAction === 'confirm-gca-health-success') {
            actionBtn.textContent = "✅ Health Check Passed";
            actionBtn.disabled = true;
            askAgent("Action: User confirmed success", currentContextUrl);
        }

        // Phase 5: GCA Selected
        if (toolAction === 'confirm-gca-selected') {
            actionBtn.textContent = "✅ GCA Selected";
            actionBtn.disabled = true;
            window.hasConfirmedGCA = true; // Set Global Flag
            askAgent("Action: User selected GCA", currentContextUrl);
        }

        // Jira OAuth: Done
        if (toolAction === 'confirm-jira-oauth-done') {
             actionBtn.textContent = "✅ Credentials Ready";
             actionBtn.disabled = true;
             askAgent("Action: confirm-jira-oauth-done", currentContextUrl);
        }

        // Retry / Check Again Logic
        if (toolAction === 'retry-field-check') {
            actionBtn.textContent = "↻ Checking...";
            actionBtn.disabled = true;
            
            // Force a re-focus simulation or just ask agent with current context
            // We use the last known Intent if available
            if (pendingIntent) {
                 askAgent("Field Guidance", "intent-trigger", pendingIntent);
            } else {
                 askAgent("Context Update: Check dropdowns again", currentContextUrl);
            }

            setTimeout(() => {
                 actionBtn.textContent = "↻ Check Again";
                 actionBtn.disabled = false;
            }, 2000);
        }
    }
});

function handleCopyClick(e) {
    // Deprecated: Logic moved to global listener above to handle dynamically added elements safely
}

async function askAgent(userMessage, contextUrl, intentObj = null) {
    if (!isAuthenticated) return;
    
    // HISTORY CHECK: Scroll to existing message instead of new one?
    // Only applies for 'field_focus' intent
    if (intentObj && intentObj.action === 'field_focus') {
        const fieldKey = intentObj.field_label + intentObj.connector; // Unique key for this field
        
        // EXCEPTION: Dynamic Fields that depend on DOM state (e.g. GCA Dropdown) should NOT use history
        const isDynamicField = intentObj.field_label.toLowerCase().includes("agent") || 
                               intentObj.field_label.toLowerCase().includes("connector");

        // GLOBAL LOCK: If user already confirmed GCA selection, ignore further focus on this field
        if (isDynamicField && window.hasConfirmedGCA) {
            console.log("Blocking GCA focus - Advice already confirmed.");
            return;
        }

        if (!isDynamicField) {
            // Check if we have a history entry for this
            const existingMsgId = window.historyMap ? window.historyMap[fieldKey] : null;

            if (existingMsgId) {
                const el = document.getElementById(existingMsgId);
                if (el) {
                    console.log("Scrolling to existing advice for:", intentObj.field_label);
                    el.scrollIntoView({ behavior: 'smooth', block: 'center' });
                    // Highlight effect
                    el.style.transition = "background-color 0.5s";
                    el.style.backgroundColor = "#fff9c4"; // light yellow
                    setTimeout(() => el.style.backgroundColor = "", 1000);
                    return; // STOP here, do not call backend
                }
            }
        }
    }
    
    // Filter out automatic triggers from chat history
    if (userMessage !== "Analyzed Page Navigation" && 
        userMessage !== "Context Update" && 
        userMessage !== "Intent Guided Help" && 
        userMessage !== "Field Guidance" &&
        userMessage !== "Action: guide-jira-oauth" &&
        userMessage !== "Action: confirm-jira-oauth-done") {
        addMessage('user', userMessage);
    } 

    try {
        // Try to fetch DOM snippet from active tab for deeper context
        let domSnippet = "";
        try {
            const [tab] = await chrome.tabs.query({active: true, currentWindow: true});
            if (tab && tab.id) {
                // Short timeout to prevent locking UI
                const p = new Promise((resolve, reject) => {
                    chrome.tabs.sendMessage(tab.id, {type: 'GET_PAGE_CONTENT'}, (res) => {
                        if (chrome.runtime.lastError) resolve("");
                        else resolve(res ? res.html : "");
                    });
                    setTimeout(() => resolve(""), 2000);
                });
                domSnippet = await p;
            }
        } catch (domErr) {
            console.warn("Could not fetch DOM snippet:", domErr);
        }

        const payload = {
            message: userMessage,
            context_url: contextUrl || currentContextUrl,
            dom_snippet: domSnippet
        };
        
        let localFieldKey = null;

        if (intentObj) {
            if (intentObj.action === 'field_focus') {
                localFieldKey = intentObj.field_label + intentObj.connector;
            }

            if (intentObj.action === 'step_progression') {
                 payload.message = `User just clicked '${intentObj.trigger_text}' to move to the next step for connector '${intentObj.connector}'. Analyze the new DOM form and valid the user inputs or guide the next step.`;
            } else if (intentObj.action === 'field_focus') {
                 payload.message = `User is currently focused on field with label: '${intentObj.field_label}'. Current value: '${intentObj.field_value}'. Connector context: '${intentObj.connector}'. Please provide specific guidance for this field only.`;
                 // No system message needed, just the answer
            } else {
                 // New Connection
                 // User 'Add' click - silent welcome, wait for focus
                 console.log("User started new connection for:", intentObj.connector);
                 return; // Do nothing until focus
            }
        }

        // Show Loading Indicator
        const loadingId = addLoadingIndicator();
getHeaders(), // Use getHeaders to include Session ID
        const response = await fetch(`${API_URL}/agent/chat`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        
        // Remove Loading Indicator
        removeMessage(loadingId);

        const data = await response.json();
        
        // Only show agent response if it's meaningful (not empty)
        if (data.response) {
            const formattedResponse = parseMarkdown(data.response);
            const newMsgId = addMessage('agent', formattedResponse);
            
            // CORRECT HISTORY MAPPING: Use local scope variable
            if (localFieldKey) {
                if (!window.historyMap) window.historyMap = {};
                window.historyMap[localFieldKey] = newMsgId;
            }
        }
    } catch (e) {
        // Remove Loading Indicator (in case of error too)
        const spinners = document.getElementsByClassName('typing-indicator');
        while(spinners.length > 0){ spinners[0].parentNode.removeChild(spinners[0]); }
        
        addMessage('system', 'Error contacting agent brain.');
    }
}

function addLoadingIndicator() {
    return addMessage('agent', '<div class="typing-indicator"><span></span><span></span><span></span></div>');
}

function removeMessage(id) {
    const el = document.getElementById(id);
    if (el) el.remove();
}

function addMessage(type, html) {
    const div = document.createElement('div');
    const msgId = `msg-${Date.now()}`;
    div.id = msgId;
    div.className = `message msg-${type}`;
    div.innerHTML = html;
    ui.chatContainer.appendChild(div);
    ui.chatContainer.scrollTop = ui.chatContainer.scrollHeight;
    
    return msgId;
}

// Start loop with a longer interval to prevent terminal spam
setInterval(checkBackend, 8000); // 8s interval
checkBackend(); // Initial check

// === Markdown Parser Function ===
function parseMarkdown(text) {
    if (!text) return text;
    
    // Step A: Extract Code Blocks
    const codeBlocks = [];
    let temp = text.replace(/```(\w*)([\s\S]*?)```/g, (match, lang, code) => {
        codeBlocks.push(code.trim());
        return `__CODE_BLOCK_${codeBlocks.length - 1}__`;
    });

    // Step B: Formating Text
    temp = temp
        .replace(/\*\*(.*?)\*\*/g, '<b>$1</b>')
        // Horizontal Rules: Must match exactly on a new line, possibly with spaces around
        .replace(/^\s*(?:---|___|\*\*\*)\s*$/gm, '<hr style="border: 0; border-top: 1px solid #e1dfdd; margin: 12px 0;">')
        // Headers: # Header 1 -> <h1>Header 1</h1>
        .replace(/^#\s+(.*)$/gm, '<h1 style="font-size: 16px; margin: 10px 0;">$1</h1>')
        .replace(/^##\s+(.*)$/gm, '<h2 style="font-size: 14px; margin: 8px 0;">$1</h2>')
        .replace(/^###\s+(.*)$/gm, '<h3 style="font-size: 13px; font-weight: bold; margin: 6px 0;">$1</h3>')
        .replace(/^####\s+(.*)$/gm, '<h4 style="font-size: 12px; font-weight: bold; margin: 4px 0;">$1</h4>')
        // Images: ![alt](url) -> <a href="url" target="_blank"><img src="url" ...></a>
        .replace(/!\[([^\]]*)\]\(([^)]+)\)/g, (match, alt, url) => {
            // Handle extension-bundled images
            if (url.startsWith("chrome-extension://__MSG_@@extension_id__/")) {
                url = chrome.runtime.getURL(url.replace("chrome-extension://__MSG_@@extension_id__/", ""));
            }
            return `<a href="${url}" target="_blank" title="Click to view larger image"><img src="${url}" alt="${alt}" style="max-width: 100%; height: auto; border: 1px solid #e1dfdd; border-radius: 4px; margin: 8px 0; display: block; cursor: zoom-in;"></a>`;
        })
        // Basic Markdown Table Parser
        // Looks for block of text starting with | (allowing whitespace/indentation)
        .replace(/((?:^\s*\|.*\|\s*(?:\r?\n|$))+)/gm, (match) => {
            const rows = match.trim().split('\n');
            if (rows.length < 2) return match; 
            
            let html = '<table style="border-collapse: collapse; width: 100%; margin: 8px 0; font-size: 11px; border: 1px solid #ddd;">';
            rows.forEach((row, index) => {
                 if (row.includes('---')) return; // Skip separator
                 const cols = row.split('|').filter((c, i, arr) => i > 0 && i < arr.length - 1); // remove first/last empty from |...| wrap
                 
                 html += '<tr>';
                 cols.forEach(col => {
                     const style = "border: 1px solid #ddd; padding: 4px 8px;";
                     if (index === 0) html += `<th style="${style} background: #f3f2f1; text-align: left;">${col.trim()}</th>`;
                     else html += `<td style="${style}">${col.trim()}</td>`;
                 });
                 html += '</tr>';
            });
            html += '</table>';
            return html;
        })
        // Add support for Markdown Links [text](url) -> <a href="url" target="_blank">text</a>
        .replace(/\[([^\]]+)\]\(([^)]+)\)/g, (match, text, url) => {
            if (url.startsWith('action:')) {
                 const action = url.split(':')[1];
                 // Use Unicode \uD83D\uDE80 for Rocket Emoji to prevent Mojibake
                 return `<button class="action-btn" data-action="${action}" style="cursor:pointer; color: white; background-color: #0078D4; border: none; padding: 4px 8px; border-radius: 4px; font-size: 11px; display: inline-flex; align-items: center; gap: 4px;">\uD83D\uDE80 ${text}</button>`;
            }
            return `<a href="${url}" target="_blank" style="color: #0078D4; text-decoration: underline;">${text}</a>`;
        })
        .replace(/\n/g, '<br>');

    // Re-inject Code Blocks with a Copy Button
    return temp.replace(/__CODE_BLOCK_(\d+)__/g, (match, index) => {
         const code = codeBlocks[index];
         return `
         <div class="suggestion-box" style="margin-top: 5px; border: 1px solid #e1dfdd; border-radius: 4px; overflow: hidden;">
            <div style="background: #f3f2f1; padding: 4px 8px; display: flex; justify-content: flex-end; border-bottom: 1px solid #e1dfdd;">
                <button class="copy-btn" data-code="${encodeURIComponent(code)}" style="cursor: pointer; border: none; background: transparent; color: #0078D4; font-size: 11px; font-weight: 600;">COPY</button>
            </div>
            <div style="background: #faf9f8; padding: 8px; font-family: Consolas, monospace; font-size: 12px; color: #333; white-space: pre-wrap;">${code}</div>
         </div>`;
    });
}
