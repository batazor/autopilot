export function PlayerHeaderCard({
  avatar,
  nickname,
  kid,
  stoveLevel,
}: {
  avatar: string;
  nickname: string;
  kid: string;
  stoveLevel: string;
}) {
  const displayName = nickname || "—";
  return (
    <section className="panel mb-4">
      <div className="flex items-center gap-4">
        {avatar ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img
            src={avatar}
            alt=""
            width={72}
            height={72}
            className="h-[72px] w-[72px] rounded-xl border border-wos-border-subtle bg-wos-panel-raised object-cover"
          />
        ) : (
          <div
            className="flex h-[72px] w-[72px] items-center justify-center rounded-xl border border-wos-border-subtle bg-wos-panel-raised text-2xl font-semibold text-wos-text-muted"
            aria-hidden
          >
            {displayName.slice(0, 1).toUpperCase()}
          </div>
        )}
        <div className="min-w-0 flex-1">
          <div className="truncate text-lg font-semibold text-wos-text">{displayName}</div>
          <div className="mt-1 flex flex-wrap items-center gap-1.5 text-xs">
            {kid ? (
              <span className="rounded-full border border-wos-border-subtle bg-wos-panel-raised px-2 py-0.5 font-medium text-wos-text-secondary">
                KID {kid}
              </span>
            ) : null}
            {stoveLevel ? (
              <span className="rounded-full border border-wos-border-subtle bg-wos-panel-raised px-2 py-0.5 font-medium text-wos-text-secondary">
                Stove {stoveLevel}
              </span>
            ) : null}
          </div>
        </div>
      </div>
    </section>
  );
}
