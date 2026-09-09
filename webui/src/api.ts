// serve HTTP API 通道（端点封闭集见 flowing.interfaces.serve.SERVE_ENDPOINTS）。

export interface AgentInfo {
  agent_id: string;
  name?: string;
  parent_agent_id?: string | null;
  last_reply?: string;
}

export interface ContentBlock {
  type: string;
  id?: string;            // tool_call
  text?: string;
  thinking?: string;
  name?: string;          // tool_call
  args?: unknown;         // tool_call
  data?: unknown;         // struct
}

export interface Message {
  id: string;
  parent_id: string | null;
  kind: string;           // user / provider / tool / event / system ...
  source?: string;
  tool_status?: string;
  content: ContentBlock[];
}

export interface TreeNode {
  id: string;
  kind: string;
  head: boolean;
  preview: string;        // 每条消息都有预览（serve /tree 提供）
  children: TreeNode[];
}

export interface Tree { head: string | null; roots: TreeNode[]; }

async function j<T>(resp: Response): Promise<T> {
  if (!resp.ok) throw new Error(`${resp.status} ${resp.statusText}`);
  return (await resp.json()) as T;
}

const enc = encodeURIComponent;

export const api = {
  agents: () => fetch("/agents").then((r) => j<AgentInfo[]>(r)),
  createAgent: () =>
    fetch("/agents", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" })
      .then((r) => j<{ agent_id: string }>(r)),
  messages: (id: string) => fetch(`/agents/${enc(id)}/messages`).then((r) => j<Message[]>(r)),
  tree: (id: string) => fetch(`/agents/${enc(id)}/tree`).then((r) => j<Tree>(r)),
  models: (id: string) =>
    fetch(`/agents/${enc(id)}/models`).then((r) => j<{ current: string | null; model_tags: string[] }>(r)),
  setModel: (id: string, tag: string) =>
    fetch(`/agents/${enc(id)}/model`, {
      method: "PATCH", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ model_tag: tag }),
    }),
  status: (id: string) =>
    fetch(`/agents/${enc(id)}/status`).then((r) => j<{
      context_usage?: { tokens: number; usage_ratio: number | null; context_window: number | null };
    }>(r)),
  send: (id: string, text: string) =>
    fetch(`/agents/${enc(id)}/message`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text }),
    }).then((r) => j<{ message_id: string; final_text: string }>(r)),
  command: (id: string, line: string) =>
    fetch(`/agents/${enc(id)}/command`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ line }),
    }).then((r) => j<{ lines: string[] }>(r)),
  rewind: (id: string, messageId: string) =>
    fetch(`/agents/${enc(id)}/rewind`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message_id: messageId }),
    }),
};
