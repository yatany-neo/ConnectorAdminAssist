// Monitor URL changes in Single Page Applications (SPA) like Admin Center
let lastUrl = location.href; 
let lastClickIntent = null; 
let sessionDetectedConnector = null; // Persist context from page titles

console.warn("[M365 Agent] Content Script Loaded - Ver 3.1");

// 1. Navigation & Page Scanning Logic
function scanPageForContext() {
    try {
        console.log("[M365 Agent] Scanning DOM...");
        
        // 1. Try URL analysis
        const url = location.href.toLowerCase();
        
        // 2. Try Header Analysis (Expanded Selectors)
        // Fluent UI uses various classes for headers
        const selectors = [
            'h1', 
            'div[role="heading"][aria-level="1"]', 
            '.ms-PageHeader-title', 
            'span.ms-PageHeader-title',
            'div[class*="header"]',
            'div[class*="title"]'
        ];
        
        let pageTitle = "";
        
        // Find the most likely "Main Title" - usually the largest or first H1
        for (const sel of selectors) {
            const el = document.querySelector(sel);
            if (el && el.innerText && el.innerText.trim().length > 3) {
                // Ignore generic titles
                const text = el.innerText.trim();
                if (text !== "Home" && text !== "Settings" && text !== "Connectors") {
                    pageTitle = text;
                    break;
                }
            }
        }

        console.log(`[M365 Agent] Page Title Identified: "${pageTitle}"`);

        // Determine Connector Context based on Page Title
        let detectedConnector = null;
        const titleLower = pageTitle.toLowerCase();

        if (titleLower.includes("jira data center")) detectedConnector = "Jira Data Center";
        else if (titleLower.includes("jira cloud")) detectedConnector = "Jira Cloud";
        else if (titleLower.includes("servicenow")) detectedConnector = "ServiceNow";
        else if (titleLower.includes("oracle")) detectedConnector = "Oracle SQL";
        else if (titleLower.includes("enterprise websites")) detectedConnector = "Enterprise Website";
        else if (titleLower.includes("file share")) detectedConnector = "File Share";
        else if (titleLower.includes("csv")) detectedConnector = "CSV import";

        // Update global session context if found
        if (detectedConnector) {
            sessionDetectedConnector = detectedConnector;
        }

        // 3. Send Update (If significant)
        if (detectedConnector || lastClickIntent) {
            const finalIntent = detectedConnector || lastClickIntent;
            console.warn(`[M365 Agent] >>> SENDING CONTEXT: ${finalIntent} <<<`);
            
            chrome.runtime.sendMessage({ 
                type: 'CONTEXT_UPDATE', 
                payload: {
                    url: location.href,
                    title: document.title,
                    page_header: pageTitle,
                    recent_intent: finalIntent
                } 
            });
        }
    } catch (e) {
        console.error("[M365 Agent] Scan Error:", e);
    }
}

// Initial Scan with a slight delay to allow React to render
setTimeout(scanPageForContext, 1500);

// Use MutationObserver to detect URL changes
new MutationObserver(() => {
  const url = location.href;
  if (url !== lastUrl) {
    console.log("[M365 Agent] Navigation Detected -> " + url);
    lastUrl = url;
    // Wait for DOM to update after navigation (SPA)
    setTimeout(scanPageForContext, 1500);
  }
}).observe(document, {subtree: true, childList: true});


// 4. Message Listener for Ext/SidePanel Communication
chrome.runtime.onMessage.addListener((request, sender, sendResponse) => {
    if (request.type === 'GET_PAGE_CONTENT') {
        const snippet = getPageContentSnippet();
        sendResponse({ html: snippet });
    }
    return true;
});

function getPageContentSnippet() {
    // Attempt to find the main content area to reduce noise
    const main = document.querySelector('main') || 
                 document.querySelector('[role="main"]') || 
                 document.querySelector('.ms-Panel-content') ||
                 document.body;

    if (!main) return "";

    // excessive cleaning to reduce tokens
    const clone = main.cloneNode(true);
    
    // CAPTURE PORTALS: Append content from ms-Layer/ms-Callout (Fluent UI dropdowns)
    // Expanded selectors to catch more variations of floating content (ComboBox, ContextMenu, etc.)
    const portalSelectors = [
        '.ms-Layer', 
        '.ms-Callout', 
        'div[role="listbox"]', 
        'div[role="menu"]',
        '.ms-ComboBox-optionsContainer',
        '.ms-ContextualMenu-container'
    ];
    
    document.querySelectorAll(portalSelectors.join(', ')).forEach(layer => {
        // Simple visibility check: must have layout size
        if (layer.offsetWidth > 0 && layer.offsetHeight > 0 && layer.innerText && layer.innerText.trim().length > 0) {
            const layerClone = layer.cloneNode(true);
            // Mark it so the AI knows this is a floating layer
            layerClone.setAttribute("data-source", "floating-layer");
            clone.appendChild(layerClone);
        }
    });
    
    // Remove scripts, styles, svgs, hidden elements
    const toRemove = clone.querySelectorAll('script, style, svg, [hidden], noscript');
    toRemove.forEach(el => el.remove());

    // Simple truncation if too large (approx 10k chars)
    let html = clone.innerHTML;
    // Collapse whitespace
    html = html.replace(/\s+/g, ' ').trim();
    
    return html.substring(0, 15000); 
}

// 3. Field Focus Tracking (Micro-Guidance)
document.addEventListener('focusin', (e) => {
    try {
        const target = e.target;
        const tagName = target.tagName;
        const role = target.getAttribute('role');
        
        // Expanded to include Fluent UI ComboBoxes (div/button with role="combobox")
        const isInteractive = ['INPUT', 'TEXTAREA', 'SELECT'].includes(tagName) || 
                              role === 'combobox' || 
                              role === 'listbox';

        if (isInteractive) {
             
             // Debug logging to help verify it's working
             console.log(`[M365 Agent DEBUG] Focus detected on <${tagName} role=${role}> params:`, target.id, target.className);

             // Avoid focusing on search bars or generic navigation
             if (target.getAttribute('type') === 'search') return;

             // Find a label
             let labelText = "";
             
             // 1. Try aria-label
             labelText = target.getAttribute('aria-label');

             // 2. Try aria-labelledby (Fluent UI Standard)
             if (!labelText && target.getAttribute('aria-labelledby')) {
                 const idList = target.getAttribute('aria-labelledby').split(' ');
                 const labelParts = idList.map(id => {
                     const el = document.getElementById(id);
                     return el ? el.innerText : '';
                 });
                 labelText = labelParts.join(' ').trim();
             }

             // 3. Try explicit label wrapper or 'for' attribute
             if (!labelText && target.id) {
                 const labelEl = document.querySelector(`label[for="${target.id}"]`);
                 if (labelEl) labelText = labelEl.innerText;
             }

             // 4. Try placeholder as last resort
             if (!labelText) {
                 labelText = target.getAttribute('placeholder');
             }

             // 5. Recursive Parent Search (Improved for Nested Layouts)
             if (!labelText) {
                 let curr = target;
                 // Go up 4 levels to find a container with text (e.g., ms-TextField-wrapper)
                 for (let i=0; i<4; i++) {
                     if (!curr.parentElement) break;
                     curr = curr.parentElement;
                     
                     // Get all text but try to exclude the input's own value
                     const textContent = curr.innerText;
                     // Increased limit to 250 to scan larger wrapper components
                     if (textContent && textContent.length < 250) {
                        // Extract first meaningful line
                        const candidate = textContent.split('\n')[0].trim();
                        // ensure candidate is not just the input value itself
                        const val = (target.value || "").toLowerCase();
                        if (candidate && candidate.length > 2 && candidate.toLowerCase() !== val) {
                            labelText = candidate;
                            break; 
                        }
                     }
                 }
             }
             
             // Sanitize (Remove * for required fields)
             if (labelText) labelText = labelText.replace(/\*/g, '').trim();

             // Filtering: Ignore generic Search boxes and specific excluded fields
             const lowerLabel = labelText ? labelText.toLowerCase() : "";
             
             // Broader filter: "search" OR ("jira" AND "url") OR "data sources search box"
             // Updated per user request to ignore "data sources search box" response
             if (lowerLabel === 'search' || 
                 lowerLabel.includes('search box') || 
                 (lowerLabel.includes('jira') && lowerLabel.includes('url'))) {
                 console.log("[M365 Agent] Ignoring excluded field focus:" + lowerLabel);
                 return;
             }

             console.log(`[M365 Agent DEBUG] Resolved Label: "${labelText || 'N/A'}"`);

             if (labelText && labelText.length < 100 && labelText.length > 2) { 
                 
                 // DEDUPLICATION: Stronger check using the Element itself
                 const now = Date.now();
                 // If identifying the same DOM element (or same label very quickly), ignore
                 // Reduced from 3000ms to 1000ms to allow quicker re-focus for highlighting
                 if ((window.lastFocusTarget === target || window.lastFocusLabel === labelText) && (now - (window.lastFocusTime || 0) < 1000)) {
                     console.log("[M365 Agent] Skipping duplicate focus event");
                     return;
                 }
                 window.lastFocusTarget = target;
                 window.lastFocusLabel = labelText;
                 window.lastFocusTime = now;

                 // Smart Context Recovery
                 if (!sessionDetectedConnector) {
                     // Try to grab from visible header if session is lost
                     const header = document.querySelector('h1')?.innerText || "";
                     if (header.includes("Jira")) sessionDetectedConnector = "Jira Data Center";
                     else if (header.includes("ServiceNow")) sessionDetectedConnector = "ServiceNow";
                 }

                 // Extract Current Value intelligently 
                 // Fix: Fluent UI 'div' comboboxes don't have .value, use innerText
                 let currentValue = target.value;
                 if (!currentValue && (target.getAttribute('role') === 'combobox' || target.getAttribute('role') === 'listbox')) {
                     currentValue = target.innerText;
                 }
                 currentValue = (currentValue || "").trim();

                 try {
                     if (!chrome.runtime?.id) throw new Error("Extension context invalidated (Pre-check)");
                     
                     chrome.runtime.sendMessage({
                        type: 'USER_INTENT',
                        payload: {
                            action: 'field_focus',
                            field_label: labelText,
                            field_value: currentValue, 
                            connector: lastClickIntent || sessionDetectedConnector || document.title || "Unknown Context", 
                            timestamp: Date.now()
                        }
                     }, (response) => {
                         // Optional: Handle response
                         if (chrome.runtime.lastError) {
                             console.warn("[M365 Agent] Message sending warning:", chrome.runtime.lastError.message);
                         }
                     });
                 } catch (err) {
                     if (err.message.includes("Extension context invalidated")) {
                         console.error("[M365 Agent] Extension triggered a reload. Please refresh this page to reconnect.");
                     } else {
                         console.error("[M365 Agent] Runtime Error:", err);
                     }
                 }
             }
        }
    } catch (e) { 
        console.error("[M365 Agent] Focus Error:", e);
    }
}, true);

// 2. Click Intent Logic (Backup)
document.addEventListener('click', (e) => {
  try {
      const target = e.target;
      const button = target.closest('button, a, [role="button"], div.ms-Button, div[class*="card"]');
      
      if (button) {
        const btnText = (button.innerText || button.getAttribute('aria-label') || "").toLowerCase();
        
        if (btnText.includes('add') || btnText.includes('setup') || btnText.includes('next') || btnText.includes('save') || btnText.includes('validate')) {
             
             // 1. Detect Navigation/Progress Clicks
             if (btnText.includes('next') || btnText.includes('save') || btnText.includes('validate')) {
                 console.log(`[M365 Agent] Step Progression Detected: ${btnText}`);
                 chrome.runtime.sendMessage({
                    type: 'USER_INTENT',
                    payload: {
                        action: 'step_progression',
                        trigger_text: btnText,
                        connector: lastClickIntent, // Persist the known context
                        timestamp: Date.now()
                    }
                 });
                 return; // specific handler done
             }

             // 2. Detect New Connection Start
             // Quick scan of the card text
             let parent = button.parentElement;
             for (let i = 0; i < 7; i++) {
                if (!parent) break;
                const txt = (parent.innerText || "").toLowerCase();
                if (txt.includes("jira data center")) { lastClickIntent = "Jira Data Center"; break; }
                if (txt.includes("servicenow")) { lastClickIntent = "ServiceNow"; break; }
                if (txt.includes("imanage")) { lastClickIntent = "iManage"; break; }
                if (txt.includes("confluence")) { lastClickIntent = "Confluence"; break; }
                if (txt.includes("salesforce")) { lastClickIntent = "Salesforce"; break; }
                if (txt.includes("azure devops")) { lastClickIntent = "Azure DevOps"; break; }
                if (txt.includes("oracle")) { lastClickIntent = "Oracle"; break; }
                parent = parent.parentElement;
             }
             if (lastClickIntent) {
                 console.log("[M365 Agent] Click Intent Captured: " + lastClickIntent);
                 
                 // Send immediate intent signal to Side Panel
                 chrome.runtime.sendMessage({
                    type: 'USER_INTENT',
                    payload: {
                        action: 'initialize_connection',
                        connector: lastClickIntent,
                        timestamp: Date.now()
                    }
                 });
             }
        }
      }
  } catch(e) {}
}, true);
