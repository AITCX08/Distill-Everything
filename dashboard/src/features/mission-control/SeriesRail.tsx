type PartStatus = "已完成" | "失败" | "执行中" | "等待处理";

function statusFor(rowId: number, completed: number, active: number, failed: number): PartStatus {
  if (rowId <= completed) return "已完成";
  if (rowId <= completed + failed) return "失败";
  if (rowId <= completed + failed + active) return "执行中";
  return "等待处理";
}

function markFor(status: PartStatus): string | null {
  if (status === "已完成") return "✓";
  if (status === "失败") return "!";
  return null;
}

export function SeriesRail({
  total,
  completed,
  active,
  failed,
  selectedRowId,
  onSelect,
}: {
  total: number;
  completed: number;
  active: number;
  failed: number;
  selectedRowId: number | null;
  onSelect: (rowId: number) => void;
}) {
  return <section className="series-rail" aria-label="系列进度">
    <div className="series-rail__heading"><span>作品轨道</span><span>{total} 集</span></div>
    <ol className="series-rail__list">
      {Array.from({ length: total }, (_, index) => {
        const rowId = index + 1;
        const status = statusFor(rowId, completed, active, failed);
        const mark = markFor(status);
        return <li className="series-rail__part" key={rowId}>
          <button
            type="button"
            className="series-rail__item"
            data-status={status}
            title={`第 ${rowId} 集 · ${status}`}
            aria-label={`第 ${rowId} 集，${status}`}
            aria-pressed={selectedRowId === rowId}
            onClick={() => onSelect(rowId)}
          >
            <span className="series-rail__number">{rowId}</span>
            {mark && <span className="series-rail__marker" aria-hidden="true">{mark}</span>}
          </button>
        </li>;
      })}
    </ol>
  </section>;
}
