using System.Linq;
public class Svc
{
    public bool HasItems(int[] items)
    {
        return items.Count() > 0;
    }
}
