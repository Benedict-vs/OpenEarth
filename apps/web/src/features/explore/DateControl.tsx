import { useDateStore } from "../../stores/dateStore";

export function DateControl() {
  const { mode, start, end, targetDate, halfWindowDays } = useDateStore();
  const { setMode, setRange, setTargetDate, setHalfWindowDays } = useDateStore.getState();

  return (
    <div className="date-control">
      <div className="roi-buttons">
        <button className={mode === "range" ? "active" : ""} onClick={() => setMode("range")}>
          Range
        </button>
        <button
          className={mode === "single" ? "active" : ""}
          title="Mean composite over a single date ± window"
          onClick={() => setMode("single")}
        >
          Single date
        </button>
      </div>
      {mode === "range" ? (
        <div className="date-inputs">
          <label>
            From
            <input
              type="date"
              value={start}
              max={end}
              onChange={(event) => setRange(event.target.value, end)}
            />
          </label>
          <label>
            To
            <input
              type="date"
              value={end}
              min={start}
              onChange={(event) => setRange(start, event.target.value)}
            />
          </label>
        </div>
      ) : (
        <div className="date-inputs">
          <label>
            Date
            <input
              type="date"
              value={targetDate}
              onChange={(event) => setTargetDate(event.target.value)}
            />
          </label>
          <label>
            ± days
            <input
              type="number"
              min={0}
              max={30}
              value={halfWindowDays}
              onChange={(event) => setHalfWindowDays(Number(event.target.value))}
            />
          </label>
        </div>
      )}
    </div>
  );
}
