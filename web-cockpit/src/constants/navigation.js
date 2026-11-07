/**
 * Navigation & Cockpit Constants (SOLID: Single Responsibility & Open/Closed)
 * Centralizes all navigation items, desktop modes, and quick prompts in one place.
 */

export const NAVIGATION_TABS = [
  {
    id: "concierge",
    label: "Concierge",
    icon: "💬",
    testId: "mobile-nav-concierge",
    isPrimary: true,
  },
  {
    id: "board",
    label: "Board",
    icon: "📋",
    testId: "mobile-nav-board",
    isPrimary: true,
  },
  {
    id: "approvals",
    label: "Approvals",
    icon: "🔒",
    testId: "mobile-nav-approvals",
    badgeKey: "pendingApprovals",
    isPrimary: true,
  },
  {
    id: "agents",
    label: "Agents",
    icon: "🤖",
    testId: "mobile-nav-agents",
    isPrimary: true,
  },
  {
    id: "more",
    label: "More",
    icon: "•••",
    testId: "mobile-nav-more",
    isPrimary: true,
  },
];

export const MORE_DRAWER_ITEMS = [
  {
    id: "workflows",
    label: "BPMN 2.0 Workflows",
    icon: "⚡",
    description: "Visual state graphs & step-by-step pipeline runner",
    testId: "more-item-workflows",
  },
  {
    id: "tools",
    label: "Skills & Tools Registry",
    icon: "🛠️",
    description: "MCP tool capability contracts & dynamic synthesis",
    testId: "more-item-tools",
  },
  {
    id: "guide",
    label: "User Guide & Architecture",
    icon: "📖",
    description: "Homelab cluster topology & system invariants",
    testId: "more-item-guide",
  },
];

export const DESKTOP_MODES = [
  { id: "home", label: "💬 Cockpit Home" },
  { id: "split", label: "3-Column Split" },
  { id: "board", label: "Focus Board" },
  { id: "workflows", label: "Focus BPMN" },
  { id: "agents", label: "Agents", dynamicCount: "agents" },
  { id: "tools", label: "Tools", dynamicCount: "tools" },
  { id: "approvals", label: "Approvals", dynamicCount: "approvals" },
  { id: "concierge", label: "Concierge" },
  { id: "guide", label: "User Guide" },
];

export const QUICK_ACTION_PROMPTS = [
  {
    id: "synthesize_tool",
    label: "⚡ Synthesize Tool",
    prompt:
      "Create a new tool named 'system_uptime_checker' with description 'Inspect host uptime' using command 'uptime' and assign it to architect.",
    category: "meta_tooling",
  },
  {
    id: "trust_git",
    label: "🛡️ Trust Git Commands",
    prompt: "Always auto approve bash commands matching 'git *'",
    category: "policies",
  },
  {
    id: "memory_context",
    label: "🧠 Memory & Context",
    prompt:
      "Do you have memory? What are your stored memories across agents?",
    category: "memory",
  },
  {
    id: "web_search",
    label: "🌐 Web Search & NFL",
    prompt:
      "Can you search the Web? Tell me what today's date is and if there are any NFL games today",
    category: "tools",
  },
  {
    id: "bash_tools",
    label: "💻 Bash & Tools Suggestion",
    prompt:
      "We need a bash tool and some web tools. Can you suggest or add them?",
    category: "tools",
  },
  {
    id: "steer_typing",
    label: "⚡ Steer: Strict Typing",
    prompt: "steer: prioritize strict typing and robust tests",
    category: "steering",
  },
  {
    id: "list_agents",
    label: "📋 List Agents",
    prompt: "list agents and current tools",
    category: "inspection",
  },
  {
    id: "platform_state",
    label: "⚙️ Platform State",
    prompt: "inspect platform state",
    category: "inspection",
  },
  {
    id: "prune_board",
    label: "🧹 Prune Board",
    prompt: "prune blackboard entries",
    category: "maintenance",
  },
];
