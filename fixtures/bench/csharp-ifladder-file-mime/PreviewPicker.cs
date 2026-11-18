namespace Warehouse.Previews
{
    public sealed class PreviewPicker
    {
        public string Kind(string ext)
        {
            if (ext == ".pdf")
            {
                return "document";
            }
            else if (ext == ".png")
            {
                return "image";
            }
            else if (ext == ".txt")
            {
                return "text";
            }
            else if (ext == ".csv")
            {
                return "table";
            }
            return "binary";
        }
    }
}
