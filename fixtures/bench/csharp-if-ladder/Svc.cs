public class Svc
{
    public string Route(int code)
    {
        if (code == 1) { return "a"; }
        else if (code == 2) { return "b"; }
        else if (code == 3) { return "c"; }
        else if (code == 4) { return "d"; }
        return "z";
    }
}
