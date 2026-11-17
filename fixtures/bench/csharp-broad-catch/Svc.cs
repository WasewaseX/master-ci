public class Svc
{
    public void Run()
    {
        try { Work(); }
        catch (Exception ex) { Console.WriteLine("failed"); }
    }
}
