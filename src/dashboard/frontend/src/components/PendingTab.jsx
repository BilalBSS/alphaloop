import Card from './ui/Card'

// / placeholder tab shell

export default function PendingTab({ title, phase, sectionNum, hint }) {
  return (
    <section className="sec">
      <div className="sec-h">
        {sectionNum && <span className="num">{sectionNum}</span>}
        <h2>{title}<span className="punct">.</span> <em>{phase ?? 'pending'}</em></h2>
      </div>
      <Card title={`pending`} meta={phase}>
        <div className="p" style={{ marginTop: 0 }}>
          {hint ?? 'this surface lands in a follow-up phase of the ui overhaul.'}
        </div>
      </Card>
    </section>
  )
}
