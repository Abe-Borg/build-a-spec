/**
 * Delete confirmation in the template studio's Manage view.
 *
 * A confirmation belongs to ONE template: the one whose Delete… the user
 * pressed. So it is held as that template's id, never as a bare flag. A flag
 * outlived the template it was raised for: after a deletion, the next template
 * opened in Manage (an import of the same .bastemplate, a newly saved one)
 * arrived already showing Confirm delete, one click from being deleted.
 *
 * `null` means no confirmation is pending. Every way into Manage clears it, and
 * a completed deletion clears it, so this check is the second guard, not the
 * only one: even a confirmation left behind by a future code path cannot show
 * for any template but its own.
 */
export type PendingTemplateDelete = string | null;

/** Whether the Manage view for `templateId` should show Confirm delete / Keep. */
export function confirmsDeleteOf(
  pending: PendingTemplateDelete,
  templateId: string | null | undefined,
): boolean {
  return (
    typeof pending === "string" &&
    pending !== "" &&
    typeof templateId === "string" &&
    pending === templateId
  );
}
