export type Role = "user" | "assistant";

export interface ChatMessage {
  id: string;
  role: Role;
  text: string;
  streaming?: boolean;
  error?: boolean;
}

export interface Health {
  status: string;
  app: string;
  version: string;
  model: string;
  api_key_present: boolean;
}

export type StreamEvent =
  | { type: "text_delta"; text: string }
  | { type: "turn_complete"; stop_reason: string | null }
  | { type: "error"; message: string };
