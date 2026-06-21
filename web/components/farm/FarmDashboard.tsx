"use client";

import { ErrorBanner } from "@/components/feedback";
import { PageHeader } from "@/components/PageHeader";
import { Button, Icon, Pill } from "@/components/ui";
import { AccountsTable } from "./AccountsTable";
import { DeleteAccountDialog } from "./DeleteAccountDialog";
import { GeneratePanel } from "./GeneratePanel";
import { RegistrationPanel } from "./RegistrationPanel";
import { useFarmState } from "./useFarmState";

/** Full farm operator dashboard. */
export function FarmDashboard() {
  const farm = useFarmState();

  return (
    <>
      <div className="page-stack">
        <PageHeader
          title="Farm"
          actions={
            <>
              <Pill
                tone={farm.registrationTone}
                size="lg"
                dot
                pulse={farm.registrationPulse}
              >
                {farm.registrationLabel}
              </Pill>
              <Button
                variant="primary"
                className="inline-flex items-center gap-1.5"
                disabled={!farm.canStartRegistration}
                onClick={() => farm.startRegistration()}
                title={
                  farm.pending
                    ? "Registration is waiting for sdk/register"
                    : "Create character"
                }
              >
                <Icon name="plus" size="sm" />
                Create character
              </Button>
            </>
          }
        />
        {farm.error && <ErrorBanner message={farm.error} />}

        <RegistrationPanel
          pending={farm.pending}
          registrationStatus={farm.registrationStatus}
          registrationRunning={farm.registrationRunning}
          busy={farm.busy}
          logsCopied={farm.logsCopied}
          onFailed={() => farm.sendVerdict("failed")}
          onCopyLogs={farm.copyRegistrationLogs}
          onClearLogs={farm.clearRegistrationLogs}
        />

        <GeneratePanel
          count={farm.count}
          setCount={farm.setCount}
          seed={farm.seed}
          setSeed={farm.setSeed}
          busy={farm.busy}
          onGenerate={farm.generate}
        />

        <AccountsTable farm={farm} />
      </div>

      <DeleteAccountDialog
        target={farm.deleteTarget}
        setTarget={farm.setDeleteTarget}
        confirm={farm.deleteConfirm}
        setConfirm={farm.setDeleteConfirm}
        busy={farm.busy}
        onConfirm={farm.confirmRemove}
      />
    </>
  );
}
