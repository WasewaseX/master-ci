namespace Parsing
{
    public sealed class CsvParser
    {
        public Row Parse(string line)
        {
            try
            {
                return Row.Split(line);
            }
            catch (System.Exception failure)
            {
                return Row.Empty();
            }
        }
    }
}
