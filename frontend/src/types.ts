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

/* --- Document model (mirrors backend/spec_doc/model.py serialization) --- */

export type BlockStatus = "confirmed" | "assumed" | "needs_input";

export interface DocParagraph {
  id: string;
  label: string;
  text: string;
  status: BlockStatus;
  children: DocParagraph[];
}

export interface DocArticle {
  id: string;
  number: string;
  title: string;
  paragraphs: DocParagraph[];
}

export interface DocPart {
  id: string;
  number: number;
  title: string;
  articles: DocArticle[];
}

export interface SpecDoc {
  section: { number: string; title: string };
  parts: DocPart[];
  version: { index: number; count: number };
}

/** An applied edit op echoed in a doc_patch (ids are server-assigned). */
export interface DocOp {
  action: "add_article" | "add_paragraph" | "replace" | "delete";
  id: string;
  target_id?: string;
  status?: BlockStatus;
}

export interface OpenItem {
  id: string;
  element_id: string;
  ref: string;
  kind: "tbd" | "needs_input";
  label: string;
}

export interface DocPayload {
  doc: SpecDoc;
  open_questions: OpenItem[];
}

export interface ProjectLoadResult extends DocPayload {
  chat: { role: Role; text: string }[];
}

export type StreamEvent =
  | { type: "text_delta"; text: string }
  | { type: "doc_patch"; ops: DocOp[]; doc: SpecDoc }
  | { type: "doc_snapshot"; doc: SpecDoc }
  | { type: "open_questions"; items: OpenItem[] }
  | { type: "turn_complete"; stop_reason: string | null }
  | { type: "error"; message: string };
