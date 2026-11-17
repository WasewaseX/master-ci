object Runner {
  def run(): Unit =
    try { work() }
    catch { case e: Exception => log(e) }
}
