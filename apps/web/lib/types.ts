export type User = { id: string; email: string; name: string; role: string };

export type AdminUser = User & { is_active: boolean };

export type Topic = { id: string; name: string; slug: string; doc_count: number };

export type Doc = {
  id: string;
  filename: string;
  mime: string;
  size_bytes: number;
  source_kind: string;
  status: string;
  error: string | null;
  page_count: number | null;
  duration_ms: number | null;
  summary: string | null;
  key_entities: string[] | null;
  suggested_questions: string[] | null;
  topics: Topic[];
  uploader: User | null;
  created_at: string;
  has_pdf: boolean;
  has_media: boolean;
};

export type Page = {
  page_no: number;
  width: number;
  height: number;
  rotation: number;
  has_text_layer: boolean;
};

export type DocDetail = Doc & {
  pages: Page[];
  outline: { page_no: number | null; level: number | null; text: string }[];
};

/** One highlight rectangle, in PDF points with a top-left origin. */
export type Rect = { page_no: number; bbox: [number, number, number, number] };

export type Citation = {
  idx: number;
  document_id: string;
  filename: string;
  chunk_id: number | null;
  page_no: number | null;
  rects: Rect[];
  t_start_ms: number | null;
  t_end_ms: number | null;
  snippet: string;
  heading_path: string | null;
  out_of_scope: boolean;
};

export type Message = {
  id: string;
  session_id: string;
  parent_id: string | null;
  role: "user" | "assistant" | "system";
  author: User | null;
  content: string;
  status: string;
  citations: Citation[];
  created_at: string;
  sibling_index: number;
  sibling_count: number;
};

export type ChatSession = {
  id: string;
  title: string;
  folder_id: string | null;
  folder_name: string | null;
  created_by: User | null;
  active_leaf_id: string | null;
  message_count: number;
  document_count: number;
  created_at: string;
  updated_at: string;
};

export type Folder = {
  id: string;
  name: string;
  description: string | null;
  document_count: number;
  session_count: number;
  created_by: User | null;
  created_at: string;
};

export type Step = { ord: number; node: string; label: string; output?: unknown };

export type Branch = {
  message_id: string;
  preview: string;
  created_at: string;
  is_active: boolean;
};
