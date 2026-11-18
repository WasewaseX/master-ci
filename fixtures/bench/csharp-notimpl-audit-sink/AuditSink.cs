namespace Warehouse.Audit
{
    public sealed class AuditSink
    {
        public void Write(string entry)
        {
            throw new NotImplementedException("audit stream not wired to the collector yet");
        }
    }
}
