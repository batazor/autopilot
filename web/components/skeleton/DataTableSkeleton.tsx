type DataTableSkeletonProps = {
  columns: string[];
  rows?: number;
};

export function DataTableSkeleton({ columns, rows = 4 }: DataTableSkeletonProps) {
  return (
    <div className="data-table-wrap" aria-hidden>
      <table className="data-table">
        <thead>
          <tr>
            {columns.map((col) => (
              <th key={col}>{col}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {Array.from({ length: rows }, (_, rowIdx) => (
            <tr key={rowIdx}>
              {columns.map((col) => (
                <td key={col}>
                  <div
                    className={`skeleton-line ${rowIdx === 0 && col === columns[0] ? "skeleton-line--md" : "skeleton-line--sm"}`}
                  />
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
