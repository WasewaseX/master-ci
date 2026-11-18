namespace Warehouse.Audit
{
    public sealed class EventLog
    {
        public string Drain(bool pending)
        {
            var log = "";
            while (pending)
            {
                log += "[evt] ";
                pending = false;
            }
            return log;
        }
    }
}
