# Deploy Backend to Azure App Service
# Usage: .\deploy_backend.ps1 -SubscriptionId "YOUR_SUBSCRIPTION_ID"

param(
    [string]$SubscriptionId,
    [string]$ResourceGroupName = "RG-ConnectorAdmin-Prod",
    [string]$Location = "eastasia",
    [string]$AppServicePlanName = "ASP-ConnectorAdmin",
    [string]$WebAppName = "connector-backend-neo-" + (Get-Random -Minimum 1000 -Maximum 9999), # Unique name
    [string]$GitHubRepoUrl = "https://github.com/neocheng_microsoft/ConnectorAdminAssist",
    [string]$GitHubBranch = "main"
)

# 1. Login to Azure
Write-Host "Checking Azure Login..." -ForegroundColor Cyan
$currentAccount = az account show --query "user.name" -o tsv 2>$null

if ($currentAccount -like "*melusine713@gmail.com*") {
    Write-Host "Already logged in as $currentAccount." -ForegroundColor Green
} else {
    if ($currentAccount) {
        Write-Host "Currently logged in as $currentAccount. Logging out to switch accounts..." -ForegroundColor Yellow
        az logout
    }
    Write-Host "Please login to Azure (use account melusine713@gmail.com)..." -ForegroundColor Yellow
    Write-Host "Use Device Code Flow: Copy the code and paste it at https://microsoft.com/devicelogin" -ForegroundColor Cyan
    az login --use-device-code
}

if ($SubscriptionId) {
    az account set --subscription $SubscriptionId
}

Write-Host "Using Subscription: $(az account show --query name -o tsv)" -ForegroundColor Green

# 2. Create Resource Group
Write-Host "Creating Resource Group '$ResourceGroupName'..." -ForegroundColor Cyan
az group create --name $ResourceGroupName --location $Location

# 3. Create App Service Plan (Linux, Basic B1 for testing/prod)
# Note: Free tier (F1) runs on shared infrastructure and might have limitations for some features.
Write-Host "Creating App Service Plan '$AppServicePlanName'..." -ForegroundColor Cyan
az appservice plan create --name $AppServicePlanName --resource-group $ResourceGroupName --sku B1 --is-linux

# 4. Create Web App
Write-Host "Creating Web App '$WebAppName' (Runtime: Python 3.11)..." -ForegroundColor Cyan
az webapp create --name $WebAppName --resource-group $ResourceGroupName --plan $AppServicePlanName --runtime "PYTHON:3.11"

# 5. Configure Deployment Source (GitHub)
# We need a GitHub token for private repositories or CI/CD setup.
Write-Host "Configuring GitHub Deployment..." -ForegroundColor Cyan

$gitToken = $env:GITHUB_TOKEN
if (-not $gitToken) {
    if (Get-Command gh -ErrorAction SilentlyContinue) {
        try {
            $gitToken = gh auth token
        } catch {
            Write-Warning "Could not retrieve GitHub token via 'gh'. Ensure you are logged in with 'gh auth login'."
        }
    }
}

try {
    if ($gitToken) {
        az webapp deployment source config --name $WebAppName --resource-group $ResourceGroupName `
            --repo-url $GitHubRepoUrl --branch $GitHubBranch --git-token $gitToken --manual-integration
    } else {
        Write-Warning "No GitHub Token found. Attempting configuration without token..."
        az webapp deployment source config --name $WebAppName --resource-group $ResourceGroupName `
            --repo-url $GitHubRepoUrl --branch $GitHubBranch --manual-integration
    }
}
catch {
    Write-Error "Deployment source configuration failed. Please check the error message above."
    Write-Warning "You may need to manually configure the Deployment Center in the Azure Portal."
}

# 6. Configure Environment Variables from .env
Write-Host "Reading .env file and setting App Settings..." -ForegroundColor Cyan
$envFile = "backend/.env"
if (Test-Path $envFile) {
    $envContent = Get-Content $envFile
    $settings = @()
    foreach ($line in $envContent) {
        if ($line -match "^[^#]*=.*") {
            $keyVar = $line.Split("=", 2)[0].Trim()
            $valVar = $line.Split("=", 2)[1].Trim()
            # Construct key=value pair for az command
            $settings += "$keyVar=$valVar"
        }
    }
    
    # Apply settings in one go
    if ($settings.Count -gt 0) {
        az webapp config appsettings set --name $WebAppName --resource-group $ResourceGroupName --settings $settings
        Write-Host "Loaded $($settings.Count) settings from .env" -ForegroundColor Green
    }
} else {
    Write-Warning ".env file not found at $envFile"
}

# 7. Configure Startup Command
Write-Host "Setting Startup Command..." -ForegroundColor Cyan
# Since we are deploying from the 'backend' folder (via .deployment), the app is at the root of the deployment.
az webapp config set --name $WebAppName --resource-group $ResourceGroupName --startup-file "gunicorn --bind=0.0.0.0 --timeout 600 -k uvicorn.workers.UvicornWorker app:app"

# 8. Output Result
$appUrl = "https://$WebAppName.azurewebsites.net"
Write-Host "`n--------------------------------------------------" -ForegroundColor Green
Write-Host "Deployment Configuration Complete!" -ForegroundColor Green
Write-Host "Web App Name : $WebAppName"
Write-Host "URL          : $appUrl"
Write-Host "--------------------------------------------------" -ForegroundColor Green
Write-Host "Next Steps:"
Write-Host "1. Check the 'Deployment Center' in Azure Portal to ensure GitHub Actions are running."
Write-Host "2. Once deployed, update your 'extension/sidepanel.js' with the new URL."
