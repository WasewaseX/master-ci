namespace Warehouse.Billing
{
    public sealed class Invoice
    {
        public string? Note;

        public string Display()
        {
            if (Note is null)
            {
                return "";
            }
            return Note.ToUpperInvariant();
        }
    }
}
