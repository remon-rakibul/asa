"use client";

import { useEffect, useRef, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import Link from "next/link";
import {
  ArrowLeft,
  FileText,
  Trash2,
  Upload,
  AlertCircle,
  CheckCircle2,
} from "lucide-react";
import {
  deleteHospitalDocument,
  HospitalDocument,
  listHospitalDocuments,
  uploadHospitalDocument,
} from "@/lib/api";
import { useToast } from "@/lib/toast";
import ConfirmDialog from "@/components/ui/ConfirmDialog";

function fmtDate(iso: string) {
  return new Date(iso).toLocaleString("en-GB", {
    day: "2-digit", month: "short", year: "numeric",
    hour: "2-digit", minute: "2-digit",
  });
}

function fmtChunks(chunks: number) {
  return `${chunks} chunk${chunks !== 1 ? "s" : ""}`;
}

export default function HospitalDocumentsPage() {
  const { id } = useParams<{ id: string }>();
  const hospitalId = Number(id);
  const router = useRouter();

  const [docs, setDocs] = useState<HospitalDocument[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [uploading, setUploading] = useState(false);
  const [uploadMsg, setUploadMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [deletingId, setDeletingId] = useState<number | null>(null);
  const [docToDelete, setDocToDelete] = useState<HospitalDocument | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const toast = useToast();

  async function load() {
    try {
      setDocs(await listHospitalDocuments(hospitalId));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not load documents.");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { load(); }, [hospitalId]);

  async function handleUpload(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file) return;
    e.target.value = "";
    setUploading(true);
    setUploadMsg(null);
    try {
      await uploadHospitalDocument(hospitalId, file);
      setUploadMsg({ ok: true, text: `"${file.name}" ingested successfully.` });
      await load();
    } catch (err) {
      setUploadMsg({ ok: false, text: err instanceof Error ? err.message : "Upload failed." });
    } finally {
      setUploading(false);
    }
  }

  async function confirmDelete() {
    const doc = docToDelete;
    if (!doc) return;
    setDeletingId(doc.id);
    try {
      await deleteHospitalDocument(hospitalId, doc.id);
      setDocs((d) => d.filter((x) => x.id !== doc.id));
      setDocToDelete(null);
      toast.success(`Deleted "${doc.filename}"`);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Delete failed.");
    } finally {
      setDeletingId(null);
    }
  }

  return (
    <div className="h-screen w-full overflow-y-auto bg-bg">
      <header className="flex items-center gap-3 border-b border-border px-6 py-4">
        <Link href="/hospitals" className="btn-ghost btn-sm">
          <ArrowLeft size={15} />
        </Link>
        <div>
          <div className="text-sm font-semibold text-fg">Knowledge Base</div>
          <div className="text-xs text-muted">Hospital #{hospitalId} · RAG documents</div>
        </div>
        <div className="ml-auto">
          <button
            className="btn-primary btn-sm"
            onClick={() => inputRef.current?.click()}
            disabled={uploading}
          >
            <Upload size={14} />
            {uploading ? "Ingesting…" : "Upload document"}
          </button>
          <input
            ref={inputRef}
            type="file"
            accept=".txt,.md,.pdf"
            className="hidden"
            onChange={handleUpload}
          />
        </div>
      </header>

      <main className="mx-auto max-w-3xl space-y-4 p-6">
        {uploadMsg && (
          <div
            className={`card flex items-start gap-3 p-4 text-sm ${
              uploadMsg.ok ? "text-success" : "text-danger"
            }`}
          >
            {uploadMsg.ok ? (
              <CheckCircle2 size={16} className="mt-0.5 shrink-0" />
            ) : (
              <AlertCircle size={16} className="mt-0.5 shrink-0" />
            )}
            {uploadMsg.text}
          </div>
        )}

        {error && (
          <div className="card flex items-start gap-3 p-4 text-sm text-danger">
            <AlertCircle size={16} className="mt-0.5 shrink-0" />
            {error}
          </div>
        )}

        <div className="card p-4 text-xs text-muted leading-relaxed">
          Upload .txt, .md, or .pdf files. The AI booking assistant will use these
          to answer patient questions about hospital policies, visiting hours, fees,
          and services. Each file is chunked and embedded locally using Ollama
          (<code>nomic-embed-text</code>). Documents are scoped to this hospital only.
        </div>

        {loading && (
          <div className="space-y-3 animate-fade-in">
            {Array.from({ length: 3 }).map((_, i) => (
              <div
                key={i}
                className="skeleton h-16 w-full rounded-xl"
                style={{ animationDelay: `${i * 70}ms` }}
              />
            ))}
          </div>
        )}

        {!loading && docs.length === 0 && !error && (
          <div className="card flex flex-col items-center gap-4 p-10 text-center">
            <FileText size={28} className="text-faint" />
            <div>
              <div className="font-medium text-fg">No documents yet</div>
              <div className="mt-1 text-sm text-muted">
                Upload hospital policies, FAQs, or service guides.
              </div>
            </div>
            <button
              className="btn-primary btn-sm"
              onClick={() => inputRef.current?.click()}
            >
              <Upload size={14} /> Upload first document
            </button>
          </div>
        )}

        {docs.map((doc) => (
          <div
            key={doc.id}
            className="card flex items-center justify-between gap-4 p-4"
          >
            <div className="flex items-center gap-3 min-w-0">
              <span className="rounded-lg bg-surface-3 p-2 text-muted shrink-0">
                <FileText size={16} />
              </span>
              <div className="min-w-0">
                <div className="truncate text-sm font-medium text-fg">{doc.filename}</div>
                <div className="mt-0.5 text-xs text-muted">
                  {fmtChunks(doc.chunk_count)} · {fmtDate(doc.created_at)}
                </div>
              </div>
            </div>
            <button
              onClick={() => setDocToDelete(doc)}
              disabled={deletingId === doc.id}
              className="btn-ghost btn-sm text-danger hover:bg-danger/10"
              title="Delete document"
              aria-label={`Delete ${doc.filename}`}
            >
              <Trash2 size={14} />
            </button>
          </div>
        ))}
      </main>

      <ConfirmDialog
        open={docToDelete !== null}
        destructive
        title="Delete document"
        subtitle={docToDelete?.filename}
        description="This removes the document and its embeddings from the knowledge base. This cannot be undone."
        confirmLabel="Delete"
        busyLabel="Deleting…"
        busy={deletingId !== null}
        onConfirm={confirmDelete}
        onClose={() => { if (deletingId === null) setDocToDelete(null); }}
      />
    </div>
  );
}
