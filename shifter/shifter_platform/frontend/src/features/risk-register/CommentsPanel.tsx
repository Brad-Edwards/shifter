import { useState, type ReactNode } from "react";

import { useAddComment, useComments, useDeleteComment } from "@/api/risks";
import { ApiError } from "@/api/errors";
import type { Comment } from "@/api/types";
import { Alert, Button, EmptyState, Spinner, TextAreaField } from "@/ds";

import { ConfirmDialog } from "./ConfirmDialog";
import { formatTimestamp } from "./format";

function authorName(comment: Comment): string {
  const author = comment.author as { name?: string } | null | undefined;
  return author?.name ?? "Unknown";
}

export function CommentsPanel({
  riskId,
  canWrite,
  readOnly,
  includeDeleted = false,
}: Readonly<{
  riskId: number;
  canWrite: boolean;
  readOnly: boolean;
  includeDeleted?: boolean;
}>) {
  // A deleted risk is inspected with include_deleted=true; carry that through so
  // its comment history stays readable (the backend list is active-only by
  // default and 404s a deleted parent otherwise). Writes stay off via readOnly.
  const comments = useComments(riskId, includeDeleted);
  const addComment = useAddComment(riskId);
  const deleteComment = useDeleteComment(riskId);
  const [draft, setDraft] = useState("");
  const [deleteId, setDeleteId] = useState<number | null>(null);

  const addError = addComment.error instanceof ApiError ? addComment.error : null;
  const contentError = addError?.fieldErrors().content?.[0] ?? (addError && !addError.fieldErrors().content ? addError.message : undefined);

  function submit(event: React.FormEvent) {
    event.preventDefault();
    if (!draft.trim()) return;
    addComment.mutate(draft, {
      onSuccess: () => setDraft(""),
    });
  }

  const canModify = canWrite && !readOnly;

  let commentsBody: ReactNode;
  if (comments.isLoading) {
    commentsBody = <Spinner label="Loading comments" />;
  } else if (comments.isError) {
    commentsBody = (
      <Alert intent="danger" role="alert">
        Could not load comments.
      </Alert>
    );
  } else if ((comments.data?.length ?? 0) === 0) {
    commentsBody = <EmptyState title="No comments yet" />;
  } else {
    commentsBody = (
      <ul style={{ listStyle: "none", margin: 0, padding: 0, display: "grid", gap: "var(--ds-space-3)" }}>
        {comments.data?.map((comment) => (
          <li key={comment.id} className="ds-card">
            <div className="ds-card__body">
              <div className="ds-text-muted" style={{ marginBlockEnd: "var(--ds-space-1)" }}>
                {authorName(comment)} · {formatTimestamp(comment.created_at)}
              </div>
              <p style={{ whiteSpace: "pre-wrap", margin: 0 }}>{comment.content}</p>
              {canModify ? (
                <div style={{ marginBlockStart: "var(--ds-space-2)" }}>
                  <Button
                    variant="tertiary"
                    small
                    onClick={() => setDeleteId(comment.id ?? null)}
                    aria-label={`Delete comment by ${authorName(comment)} from ${formatTimestamp(comment.created_at)}`}
                  >
                    Delete
                  </Button>
                </div>
              ) : null}
            </div>
          </li>
        ))}
      </ul>
    );
  }

  return (
    <div style={{ display: "grid", gap: "var(--ds-space-4)" }}>
      {canModify ? (
        <form onSubmit={submit} style={{ display: "grid", gap: "var(--ds-space-2)" }}>
          <TextAreaField
            label="Add a comment"
            rows={3}
            value={draft}
            error={contentError}
            onChange={(event) => setDraft(event.target.value)}
          />
          <div>
            <Button type="submit" small loading={addComment.isPending} disabled={!draft.trim()}>
              Add comment
            </Button>
          </div>
        </form>
      ) : null}

      {commentsBody}

      {deleteId !== null && (
        <ConfirmDialog
          title="Delete comment?"
          confirmLabel="Delete"
          destructive
          pending={deleteComment.isPending}
          error={deleteComment.error}
          onCancel={() => {
            deleteComment.reset();
            setDeleteId(null);
          }}
          onConfirm={() =>
            deleteComment.mutate(deleteId, {
              onSuccess: () => setDeleteId(null),
            })
          }
        >
          This comment will be removed. This cannot be undone.
        </ConfirmDialog>
      )}
    </div>
  );
}
