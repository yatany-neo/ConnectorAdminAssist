// Service worker for M365 Admin Companion

// Allows the user to open the side panel by clicking the action icon
chrome.sidePanel
  .setPanelBehavior({ openPanelOnActionClick: true })
  .catch((error) => console.error(error));

chrome.runtime.onInstalled.addListener(() => {
  console.log("M365 Admin Companion Extension Installed");
});
