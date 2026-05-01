# Blender MCP Addon — Installation Guide

## Prerequisites

- Blender 3.0 or later
- The `addon.py` file downloaded to your computer

## Installation Steps

### 1. Open Blender

Launch Blender from your applications menu or desktop shortcut.

### 2. Open Preferences

Go to **Edit → Preferences** in the top menu bar.

### 3. Navigate to Add-ons

Click the **Add-ons** tab in the left sidebar of the Preferences window.

### 4. Install the Addon File

Click the **Install...** button (top-right area of the Add-ons panel).

In the file browser that opens, navigate to where you saved `addon.py` and select it, then click **Install Add-on**.

### 5. Enable the Addon

After installation, the addon will appear in the list. Find **"Blender MCP"** and check the box next to it to enable it.

> **Tip:** Use the search bar at the top of the Add-ons panel and type `Blender MCP` to find it quickly.

### 6. Save Preferences (Optional)

To keep the addon enabled across Blender sessions, click **Edit → Preferences → Hamburger menu (☰) → Save Preferences**.

## Verifying the Installation

Once enabled, the Blender MCP addon adds a panel in the **3D Viewport sidebar** (press `N` to open it) under the **MCP** tab.

## Troubleshooting

| Problem | Solution |
|---|---|
| Addon does not appear after install | Ensure you selected the correct `addon.py` file and try restarting Blender |
| Checkbox is greyed out | Check Blender's console for Python errors — the addon may have a dependency issue |
| MCP panel not visible | Press `N` in the 3D Viewport and look for the **MCP** tab in the sidebar |
| Connection errors | Verify that your MCP server is running and the URL/port are configured correctly in the addon preferences |

## Configuration

After enabling the addon, you may need to configure the MCP server connection:

1. In the Add-ons preferences, expand the **Blender MCP** entry.
2. Set the **Server URL** to your running MCP server (e.g. `http://localhost:8000/mcp`).
3. If your server uses authentication, enter the **Bearer Token** (the value of `MCP_SECRET`).

## Uninstalling

To remove the addon:

1. Go to **Edit → Preferences → Add-ons**.
2. Find **Blender MCP** and expand it.
3. Click **Remove**.
