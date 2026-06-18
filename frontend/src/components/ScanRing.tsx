export default function ScanRing() {
  return (
    <div className="scanning-state">
      <div className="scan-ring-wrap">
        <svg className="scan-ring" viewBox="0 0 80 80">
          <circle className="ring-track" cx="40" cy="40" r="34" />
          <circle className="ring-fill"  cx="40" cy="40" r="34" />
        </svg>
        <div className="scan-ring-inner">
          <span className="scan-ring-icon">◌</span>
        </div>
      </div>
      <div className="scan-ring-label">Scanning Nifty 500</div>
      <div className="scan-ring-sub">Page updates automatically</div>
    </div>
  )
}
